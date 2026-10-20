"""Host-neutral, bounded scalar expressions. Parsed syntax is data, never Python code."""

import ast
import math
import re

MAX_POINTS = 1_000_000
MAX_FIELDS = 4
MAX_ARRAY_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_OPERATIONS = 64_000_000
MAX_EXPRESSION_LENGTH = 512
MAX_EXPRESSION_NODES = 128
MAX_EXPRESSION_DEPTH = 16
MAX_EXPONENT = 8
MAX_COORDINATE = 1_000_000
NAME = r"[A-Za-z][A-Za-z0-9_-]{0,63}"
DECIMAL = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"


class ImageDataError(ValueError):
    def __init__(self, message, code="invalid_input"):
        super().__init__(message)
        self.code = code


def parse_expression(expression):
    """Return a bounded postfix arithmetic plan, with an exhaustive AST allowlist."""
    if not isinstance(expression, str) or not 1 <= len(expression) <= MAX_EXPRESSION_LENGTH:
        raise ImageDataError("Expressions must contain 1-512 characters")
    if not re.fullmatch(r"[0-9xyzexpE+\-*/(). \t]+", expression):
        raise ImageDataError("Only ASCII coordinate arithmetic tokens are supported")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        raise ImageDataError("Invalid scalar expression syntax") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_EXPRESSION_NODES:
        raise ImageDataError("Expression exceeds its syntax node budget", "resource_limit")
    instructions = []

    def visit(node, depth=1):
        if depth > MAX_EXPRESSION_DEPTH:
            raise ImageDataError("Expression exceeds its depth budget", "resource_limit")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            segment = ast.get_source_segment(expression, node)
            if not segment or not re.fullmatch(DECIMAL, segment):
                raise ImageDataError("Only finite decimal constants are supported")
            try:
                value = float(node.value)
            except OverflowError:
                raise ImageDataError("Constants must be finite Float64 values") from None
            if not math.isfinite(value):
                raise ImageDataError("Constants must be finite Float64 values")
            instructions.append(("constant", value))
        elif isinstance(node, ast.Name) and node.id in ("x", "y", "z"):
            instructions.append(("coordinate", ("x", "y", "z").index(node.id)))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            visit(node.operand, depth + 1)
            instructions.append(("positive" if isinstance(node.op, ast.UAdd) else "negative", None))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            visit(node.left, depth + 1)
            visit(node.right, depth + 1)
            instructions.append(
                ({ast.Add: "add", ast.Sub: "subtract", ast.Mult: "multiply", ast.Div: "divide"}[type(node.op)], None)
            )
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exponent = node.right
            sign = 1
            if isinstance(exponent, ast.UnaryOp) and isinstance(exponent.op, (ast.UAdd, ast.USub)):
                sign = -1 if isinstance(exponent.op, ast.USub) else 1
                exponent = exponent.operand
            if (
                not isinstance(exponent, ast.Constant)
                or type(exponent.value) is not int
                or not re.fullmatch(r"[0-9]+", ast.get_source_segment(expression, exponent) or "")
                or abs(exponent.value) > MAX_EXPONENT
            ):
                raise ImageDataError("Powers require a literal integer exponent from -8 to 8")
            visit(node.left, depth + 1)
            instructions.append(("power", sign * exponent.value))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "exp"
            and len(node.args) == 1
            and not node.keywords
        ):
            visit(node.args[0], depth + 1)
            instructions.append(("exp", None))
        else:
            raise ImageDataError("Only x/y/z, decimal constants, + - * / ** and exp are supported")

    visit(tree.body)
    return tuple(instructions)


def evaluate_expression(instructions, coordinates, significant_digits=None):
    """Evaluate only package-produced instructions, checking every Float64 intermediate."""
    stack = []
    try:
        for operation, operand in instructions:
            if operation == "constant":
                value = operand
            elif operation == "coordinate":
                value = coordinates[operand]
            elif operation in ("positive", "negative", "power", "exp"):
                a = stack.pop()
                if operation == "positive":
                    value = a
                elif operation == "negative":
                    value = -a
                elif operation == "power":
                    value = a**operand
                else:
                    value = math.exp(a)
            else:
                b, a = stack.pop(), stack.pop()
                if operation == "add":
                    value = a + b
                elif operation == "subtract":
                    value = a - b
                elif operation == "multiply":
                    value = a * b
                elif operation == "divide":
                    value = a / b
                else:
                    raise ImageDataError("Unknown internal arithmetic instruction")
            if not math.isfinite(value):
                raise ImageDataError("Scalar expression produced a nonfinite intermediate", "invalid_scalar")
            stack.append(value)
        value = stack[0]
        if significant_digits is not None:
            value = float(format(value, ".%dg" % significant_digits))
        if not math.isfinite(value):
            raise ImageDataError("Scalar quantization produced a nonfinite value", "invalid_scalar")
        return value
    except (ArithmeticError, ValueError) as error:
        if isinstance(error, ImageDataError):
            raise
        raise ImageDataError(
            "Scalar expression overflow, division by zero or invalid domain", "invalid_scalar"
        ) from None


def plan_image_data(params):
    dimensions = params["dimensions"]
    if (
        not isinstance(dimensions, list)
        or len(dimensions) != 3
        or any(type(n) is not int or not 2 <= n <= MAX_POINTS for n in dimensions)
    ):
        raise ImageDataError("Dimensions must be three integers from 2 to 1000000")
    points = math.prod(dimensions)
    if points > MAX_POINTS:
        raise ImageDataError("ImageData exceeds 1000000 points", "resource_limit")
    for key in ("origin", "spacing"):
        values = params[key]
        if (
            not isinstance(values, list)
            or len(values) != 3
            or any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or abs(v) > MAX_COORDINATE
                or not math.isfinite(v)
                or (key == "spacing" and v <= 0)
                for v in values
            )
        ):
            raise ImageDataError("Origin and positive spacing must be three bounded finite numbers")
    bounds = []
    for origin, spacing, size in zip(params["origin"], params["spacing"], dimensions):
        end = origin + (size - 1) * spacing
        if (
            not math.isfinite(end)
            or abs(end) > MAX_COORDINATE
            or end <= origin
            or spacing < math.ulp(max(abs(origin), abs(end)))
        ):
            raise ImageDataError("Derived grid coordinates must be finite, bounded and increasing")
        bounds.extend([origin, end])
    fields = params["fields"]
    if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FIELDS:
        raise ImageDataError("Provide one to four Float64 scalar fields")
    plans, names = [], []
    for field in fields:
        if not isinstance(field, dict) or set(field) != {"name", "dtype", "expression"}:
            raise ImageDataError("Each field requires exactly name, dtype and expression")
        if not isinstance(field["name"], str) or not re.fullmatch(NAME, field["name"]) or field["name"] in names:
            raise ImageDataError("Field names must be distinct short ASCII identifiers")
        if field["dtype"] != "float64":
            raise ImageDataError("Only float64 fields are supported")
        names.append(field["name"])
        plans.append(parse_expression(field["expression"]))
    active = params.get("active_scalar", names[0])
    if not isinstance(active, str) or active not in names:
        raise ImageDataError("Active scalar must name a declared field")
    precision = params.get("significant_digits")
    if "significant_digits" in params and (type(precision) is not int or not 1 <= precision <= 17):
        raise ImageDataError("significant_digits must be an integer from 1 to 17")
    if params.get("serialization", "native") not in ("native", "canonical_ascii_v1"):
        raise ImageDataError("Unsupported ImageData serialization profile")
    array_bytes = points * len(fields) * 8
    # At most 25 ASCII bytes for a signed finite Float64 plus a separator, plus bounded XML metadata.
    output_bound = points * len(fields) * 26 + 4096
    operations = points * sum(len(plan) for plan in plans)
    if array_bytes > MAX_ARRAY_BYTES or output_bound > MAX_OUTPUT_BYTES or operations > MAX_OPERATIONS:
        raise ImageDataError("ImageData exceeds its array, output or arithmetic budget", "resource_limit")
    return {
        "plans": plans,
        "points": points,
        "cells": math.prod(n - 1 for n in dimensions),
        "bounds": bounds,
        "active_scalar": active,
        "array_bytes": array_bytes,
        "output_bound": output_bound,
        "operations": operations,
    }
