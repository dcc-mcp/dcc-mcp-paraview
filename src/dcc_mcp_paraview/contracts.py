"""Host-neutral bounded argument contracts, shared by IPC client and host."""

import math
import re

MAX_SLICE_POINTS = 1_000_000
MAX_SLICE_CELLS = 1_000_000

SCALAR_BAR_CONTROLS = {
    "scalar_bar_title",
    "scalar_bar_position",
    "scalar_bar_length",
    "scalar_bar_thickness",
    "scalar_bar_title_font_size",
    "scalar_bar_label_font_size",
}

OPERATIONS = {
    "inspect_pipeline": (set(), set()),
    "inspect_presentation": (set(), set()),
    "capture_current_view": ({"path"}, set()),
    "slice_plane": ({"name", "input_name"}, {"origin", "normal"}),
    "create_sphere": ({"name"}, {"radius", "center", "resolution"}),
    "edit_sphere": ({"name", "radius"}, {"center", "resolution"}),
    "clip_plane": ({"name", "input_name"}, {"origin", "normal", "invert"}),
    "contour": ({"name", "input_name", "scalar", "values"}, set()),
    "save_state": ({"path"}, {"data_directory"}),
    "reopen_state": ({"path"}, {"data_directory"}),
    "export_dataset": ({"name", "path"}, set()),
    "open_dataset": ({"name", "path"}, set()),
    "render_preview": (
        {"name", "path"},
        {
            "width",
            "height",
            "scalar",
            "color_range",
            "preset",
            "camera_position",
            "camera_target",
            "background",
            "solid_color",
            "show_scalar_bar",
            "show_orientation_axes",
            "camera_parallel_scale",
            "camera_view_angle",
            "camera_view_up",
            "ambient",
            "diffuse",
            "specular",
            "specular_power",
            "line_width",
            *SCALAR_BAR_CONTROLS,
        },
    ),
}


class OperationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def validate(operation, params):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise OperationError("unknown_operation", "Only the declared typed operations are supported")
    required, optional = OPERATIONS[operation]
    if not isinstance(params, dict) or set(params) - required - optional or required - set(params):
        raise OperationError("invalid_input", "Unexpected or missing operation arguments")
    for key in ("name", "input_name"):
        if key in params and (
            not isinstance(params[key], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", params[key])
        ):
            raise OperationError("invalid_input", "Names must be short ASCII identifiers")
    for key in ("path", "data_directory"):
        if key in params and (
            not isinstance(params[key], str) or not 1 <= len(params[key]) <= 4096 or "\x00" in params[key]
        ):
            raise OperationError("invalid_input", "A bounded file or directory path is required")
    if "radius" in params:
        radius = params["radius"]
        if (
            isinstance(radius, bool)
            or not isinstance(radius, (int, float))
            or abs(radius) > 10000
            or not math.isfinite(radius)
        ):
            raise OperationError("invalid_input", "Radius must be finite")
        if not 0.0001 <= radius <= 10000:
            raise OperationError("invalid_input", "Radius must be between 0.0001 and 10000")
    for key in (
        "center",
        "origin",
        "normal",
        "camera_position",
        "camera_target",
        "background",
        "solid_color",
        "camera_view_up",
    ):
        if key in params:
            value = params[key]
            if (
                not isinstance(value, list)
                or len(value) != 3
                or any(
                    isinstance(n, bool) or not isinstance(n, (int, float)) or abs(n) > 1e6 or not math.isfinite(n)
                    for n in value
                )
            ):
                raise OperationError("invalid_input", "Vectors must contain three bounded finite numbers")
            zero_vector = not any(value) if operation == "slice_plane" else sum(n * n for n in value) < 1e-12
            if key in {"normal", "camera_view_up"} and zero_vector:
                raise OperationError("invalid_input", "Plane normal must be nonzero")
    for key, low, high in (("resolution", 8, 128), ("width", 64, 2048), ("height", 64, 2048)):
        if key in params and (type(params[key]) is not int or not low <= params[key] <= high):
            raise OperationError("invalid_input", "%s must be an integer in [%d, %d]" % (key, low, high))
    for key, low, high in (
        ("scalar_bar_thickness", 1, 64),
        ("scalar_bar_title_font_size", 6, 48),
        ("scalar_bar_label_font_size", 6, 48),
    ):
        if key in params and (type(params[key]) is not int or not low <= params[key] <= high):
            raise OperationError("invalid_input", "%s must be an integer in [%d, %d]" % (key, low, high))
    if "scalar_bar_title" in params:
        title = params["scalar_bar_title"]
        if (
            not isinstance(title, str)
            or not 1 <= len(title) <= 80
            or any(ord(c) < 32 or ord(c) > 126 or c in "$\\{}" for c in title)
        ):
            raise OperationError("invalid_input", "Legend title must be 1-80 plain ASCII characters without markup")
    if "scalar_bar_position" in params:
        position = params["scalar_bar_position"]
        if (
            not isinstance(position, list)
            or len(position) != 2
            or any(
                isinstance(n, bool) or not isinstance(n, (int, float)) or not 0 <= n <= 1 or not math.isfinite(n)
                for n in position
            )
        ):
            raise OperationError("invalid_input", "Legend position must contain two finite numbers in [0, 1]")
    if SCALAR_BAR_CONTROLS.intersection(params) and "scalar" not in params:
        raise OperationError("invalid_input", "Legend controls require scalar coloring")
    if "invert" in params and type(params["invert"]) is not bool:
        raise OperationError("invalid_input", "invert must be a boolean")
    if "scalar" in params and (
        not isinstance(params["scalar"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,63}", params["scalar"])
    ):
        raise OperationError("invalid_input", "Scalar name must be a bounded identifier")
    for key in ("values", "color_range"):
        if key in params:
            value = params[key]
            if (
                not isinstance(value, list)
                or not 1 <= len(value) <= 8
                or any(
                    isinstance(n, bool) or not isinstance(n, (int, float)) or abs(n) > 1e9 or not math.isfinite(n)
                    for n in value
                )
            ):
                raise OperationError("invalid_input", "Contour levels and color ranges must be bounded finite numbers")
            if key == "color_range" and (len(value) != 2 or value[0] >= value[1]):
                raise OperationError("invalid_input", "Color range must be [minimum, maximum]")
    if "preset" in params and (
        not isinstance(params["preset"], str)
        or params["preset"]
        not in {
            "Cool to Warm (Extended)",
            "Viridis (matplotlib)",
            "Inferno (matplotlib)",
            "Black-Body Radiation",
        }
    ):
        raise OperationError("invalid_input", "Unsupported color preset")
    for key in ("background", "solid_color"):
        if key in params and any(n < 0 or n > 1 for n in params[key]):
            raise OperationError("invalid_input", "RGB channels must be in [0, 1]")
    if "solid_color" in params and "scalar" in params:
        raise OperationError("invalid_input", "Choose scalar coloring or solid_color, not both")
    for key in ("show_scalar_bar", "show_orientation_axes"):
        if key in params and type(params[key]) is not bool:
            raise OperationError("invalid_input", "%s must be a boolean" % key)
    for key, low, high in (
        ("camera_parallel_scale", 0.001, 1e6),
        ("camera_view_angle", 1, 120),
        ("ambient", 0, 1),
        ("diffuse", 0, 1),
        ("specular", 0, 1),
        ("specular_power", 1, 128),
        ("line_width", 1, 8),
        ("scalar_bar_length", 0.05, 0.9),
    ):
        if key in params:
            value = params[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not low <= value <= high
                or not math.isfinite(value)
            ):
                raise OperationError("invalid_input", "%s must be finite and in [%s, %s]" % (key, low, high))
    if "scalar_bar_position" in params and "scalar_bar_length" in params:
        if params["scalar_bar_position"][1] + params["scalar_bar_length"] > 1:
            raise OperationError("invalid_input", "Vertical legend position plus length must fit inside the view")
    if "color_range" in params and "scalar" not in params:
        raise OperationError("invalid_input", "color_range requires a scalar")
    if params.get("camera_position") is not None and params.get("camera_target") is not None:
        if sum((a - b) ** 2 for a, b in zip(params["camera_position"], params["camera_target"])) < 1e-12:
            raise OperationError("invalid_input", "Camera position and target must differ")
    return params
