"""Package-owned typed IPC driver. Run by pvpython; never imported by HTTP workers."""

import hashlib
import json
import math
import os
import re
import sys
import threading
import uuid
from pathlib import Path
from xml.etree import ElementTree

from contracts import MAX_SLICE_CELLS, MAX_SLICE_POINTS, SCALAR_BAR_CONTROLS, OperationError, validate

MAX_FILE_BYTES = 64 * 1024 * 1024


class Host:
    def __init__(self, root):
        # Importing ParaView outside this child main thread is deliberately forbidden.
        from paraview import servermanager, simple

        self.pv = simple
        self.root = Path(root).resolve(strict=True)
        self.thread_id = threading.get_ident()
        self.saved_states = {}
        self.view = None
        self.version = ".".join(
            str(getattr(servermanager.vtkSMProxyManager, "GetVersion" + part)()) for part in ("Major", "Minor", "Patch")
        )

    def path(self, value, suffix, output=False):
        path = Path(value)
        path = self.root / path if not path.is_absolute() else path
        # Check the lexical path before resolution so even in-root and dangling aliases fail closed.
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise OperationError("path_denied", "File must remain inside the configured workspace") from None
        current = self.root
        for component in relative.parts:
            if component == "..":
                raise OperationError("path_denied", "Parent traversal is unsupported")
            current = current / component
            if current.is_symlink():
                raise OperationError("path_denied", "Symlink components are unsupported")
        path = path.resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            raise OperationError("path_denied", "File must remain inside the configured workspace") from None
        if path.suffix.lower() != suffix:
            raise OperationError("invalid_input", "Expected a %s file" % suffix)
        if output:
            if path.exists():
                raise OperationError("output_exists", "Output exists; choose a new path")
            if not path.parent.is_dir():
                raise OperationError("invalid_input", "Output parent directory must already exist")
        elif not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise OperationError("invalid_input", "Input is missing or larger than 64 MiB")
        return path

    def source(self, name):
        source = self.pv.FindSource(name)
        if source is None:
            raise OperationError("source_not_found", "No source has that name")
        return source

    def new_name(self, name):
        if self.pv.FindSource(name) is not None:
            raise OperationError("name_exists", "A source already has that name")
        if len(self.pv.GetSources()) >= 32:
            raise OperationError("resource_limit", "This bounded session permits at most 32 sources")

    def info(self, name, source):
        source.UpdatePipeline()
        data = source.GetDataInformation()
        result = {
            "name": name,
            "type": source.SMProxy.GetXMLName(),
            "points": data.GetNumberOfPoints(),
            "cells": data.GetNumberOfCells(),
            "bounds": list(data.GetBounds()) if data.GetNumberOfPoints() else [],
        }
        result["point_arrays"] = [
            {
                "name": key,
                "components": source.PointData[key].GetNumberOfComponents(),
                "range": list(source.PointData[key].GetRange()),
            }
            for key in list(source.PointData.keys())[:32]
        ]
        if result["type"] == "SphereSource":
            result.update(
                radius=float(source.Radius), center=list(source.Center), resolution=int(source.ThetaResolution)
            )
        if result["type"] == "Clip":
            result.update(
                origin=list(source.ClipType.Origin), normal=list(source.ClipType.Normal), invert=bool(source.Invert)
            )
        if result["type"] == "Cut":
            result.update(
                origin=list(source.SliceType.Origin),
                normal=list(source.SliceType.Normal),
                offsets=list(source.SliceOffsetValues),
                crinkle=bool(source.Crinkleslice),
                triangulate=bool(source.Triangulatetheslice),
                input_bindings=self._bindings(source, self.pv.GetSources()),
            )
        return result

    def inspect_pipeline(self):
        return {
            "sources": [self.info(key[0], source) for key, source in sorted(self.pv.GetSources().items())],
            "host": {
                "pid": os.getpid(),
                "thread_id": threading.get_ident(),
                "main_thread_id": self.thread_id,
                "version": self.version,
                "render_available": bool(os.environ.get("DISPLAY")),
                "render_prerequisite": "DISPLAY is present; context creation is not guaranteed"
                if os.environ.get("DISPLAY")
                else "DISPLAY is absent",
            },
        }

    def create_sphere(self, name, radius=1.0, center=None, resolution=32):
        self.new_name(name)
        source = self.pv.Sphere(registrationName=name)
        source.Radius = radius
        source.Center = center or [0, 0, 0]
        source.ThetaResolution = source.PhiResolution = resolution
        actual = self.info(name, source)
        if (
            actual["radius"] != radius
            or actual["center"] != (center or [0, 0, 0])
            or actual["resolution"] != resolution
        ):
            raise OperationError("verification_failed", "Sphere parameter readback differs")
        return {"source": actual}

    def edit_sphere(self, name, radius, center=None, resolution=None):
        source = self.source(name)
        if source.SMProxy.GetXMLName() != "SphereSource":
            raise OperationError("wrong_source_type", "Only a sphere can be edited by this tool")
        source.Radius = radius
        if center is not None:
            source.Center = center
        if resolution is not None:
            source.ThetaResolution = source.PhiResolution = resolution
        actual = self.info(name, source)
        if (
            actual["radius"] != radius
            or (center is not None and actual["center"] != center)
            or (resolution is not None and actual["resolution"] != resolution)
        ):
            raise OperationError("verification_failed", "Sphere parameter readback differs")
        return {"source": actual}

    def clip_plane(self, name, input_name, origin=None, normal=None, invert=False):
        self.new_name(name)
        source = self.pv.Clip(registrationName=name, Input=self.source(input_name))
        source.ClipType = "Plane"
        source.ClipType.Origin = origin or [0, 0, 0]
        source.ClipType.Normal = normal or [1, 0, 0]
        source.Invert = int(invert)
        actual = self.info(name, source)
        if actual["origin"] != (origin or [0, 0, 0]) or actual["normal"] != (normal or [1, 0, 0]):
            raise OperationError("verification_failed", "Clip plane readback differs")
        if actual["invert"] != invert:
            raise OperationError("verification_failed", "Clip inversion readback differs")
        return {"source": actual}

    def slice_plane(self, name, input_name, origin=None, normal=None):
        # Repeat the complete contract for direct host calls before any native mutation.
        params = {"name": name, "input_name": input_name}
        if origin is not None:
            params["origin"] = origin
        if normal is not None:
            params["normal"] = normal
        validate("slice_plane", params)
        self.new_name(name)
        source = self.source(input_name)
        before = self.info(input_name, source)
        if before["points"] > MAX_SLICE_POINTS or before["cells"] > MAX_SLICE_CELLS:
            raise OperationError("resource_limit", "Slice input exceeds the bounded geometry limit")
        if len(source.PointData.keys()) > 32:
            raise OperationError("resource_limit", "Slice supports at most 32 point arrays")
        expected_arrays = {a["name"]: a["components"] for a in before["point_arrays"]}
        plane_origin, plane_normal = origin or [0, 0, 0], normal or [1, 0, 0]
        active = self.pv.GetActiveSource()
        output = None
        try:
            output = self.pv.Slice(registrationName=name, Input=source)
            output.SliceType = "Plane"
            output.SliceType.Origin = plane_origin
            output.SliceType.Normal = plane_normal
            output.SliceOffsetValues = [0.0]
            output.Crinkleslice = 0
            output.Triangulatetheslice = 1
            actual = self.info(name, output)
            if (
                actual.get("origin") != plane_origin
                or actual.get("normal") != plane_normal
                or actual.get("offsets") != [0.0]
                or actual.get("crinkle") is not False
                or actual.get("triangulate") is not True
                or actual.get("input_bindings") != [{"source_names": [input_name], "port": 0}]
            ):
                raise OperationError("verification_failed", "Native slice plane readback differs")
            if actual["points"] > MAX_SLICE_POINTS or actual["cells"] > MAX_SLICE_CELLS:
                raise OperationError("resource_limit", "Slice output exceeds the bounded geometry limit")
            if not actual["points"] or not actual["cells"]:
                raise OperationError("empty_slice", "The plane does not intersect a nonempty section")
            if output.GetDataInformation().GetDataSetType() != 0:  # VTK_POLY_DATA
                raise OperationError("verification_failed", "Slice output is not polygonal section data")
            arrays = {a["name"]: a["components"] for a in actual["point_arrays"]}
            if arrays != expected_arrays or any(not math.isfinite(v) for v in actual["bounds"]):
                raise OperationError("verification_failed", "Slice point arrays or bounds differ")
            if any(not math.isfinite(v) for a in actual["point_arrays"] for v in a["range"]):
                raise OperationError("verification_failed", "Slice point array ranges are not finite")
            actual["input_name"] = actual["input_bindings"][0]["source_names"][0]
            return {"source": actual}
        except BaseException:
            # A native constructor may register a proxy before raising.
            created = output if output is not None else self.pv.FindSource(name)
            if created is not None:
                self.pv.Delete(created)
            raise
        finally:
            self.pv.SetActiveSource(active)

    def scalar(self, source, scalar):
        source.UpdatePipeline()
        if scalar not in source.PointData.keys() or source.PointData[scalar].GetNumberOfComponents() != 1:
            raise OperationError("invalid_scalar", "A named, single-component point-data scalar is required")

    def contour(self, name, input_name, scalar, values):
        self.new_name(name)
        source = self.source(input_name)
        self.scalar(source, scalar)
        output = self.pv.Contour(registrationName=name, Input=source)
        output.ContourBy = ["POINTS", scalar]
        output.Isosurfaces = values
        actual = self.info(name, output)
        if list(output.Isosurfaces) != values or list(output.ContourBy) != ["POINTS", scalar]:
            raise OperationError("verification_failed", "Contour parameter readback differs")
        actual.update(scalar=scalar, values=list(output.Isosurfaces))
        return {"source": actual}

    def publish(self, path, writer, verifier):
        staged = path.with_name("." + path.stem + "." + uuid.uuid4().hex + path.suffix)
        try:
            writer(str(staged))
            if not staged.is_file() or not 0 < staged.stat().st_size <= MAX_FILE_BYTES:
                raise OperationError("verification_failed", "Host did not produce a bounded nonempty artifact")
            evidence = verifier(staged)
            # Atomic no-clobber publication, including concurrent creation of the destination.
            os.link(str(staged), str(path))
            return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path), **evidence}
        except FileExistsError:
            raise OperationError("output_exists", "Output was created concurrently; existing file preserved") from None
        finally:
            if staged.exists():
                staged.unlink()

    def _state_datasets(self):
        """Record only the single-file readers this typed adapter can create."""
        result = []
        allowed = {"XMLPolyDataReader": ".vtp", "XMLUnstructuredGridReader": ".vtu", "XMLImageDataReader": ".vti"}
        for key, source in sorted(self.pv.GetSources().items()):
            kind = source.SMProxy.GetXMLName()
            if source.GetProperty("FileName") is None:
                continue
            if kind not in allowed or len(list(source.FileName)) != 1:
                raise OperationError(
                    "unsupported_reader", "State relocation supports typed single-file XML readers only"
                )
            path = self.path(list(source.FileName)[0], allowed[kind])
            result.append({"name": key[0], "path": str(path), "filename": path.name, "sha256": digest(path)})
        return result

    def save_state(self, path, data_directory=None):
        output = self.path(path, ".pvsm", output=True)
        expected = self.inspect_pipeline()["sources"]
        datasets = self._state_datasets()
        original_paths = {item["name"]: item["path"] for item in datasets}
        folder = self._relocation_directory(data_directory) if data_directory is not None else None
        relative_paths = {}
        if folder is not None:
            try:
                folder.relative_to(output.parent)
            except ValueError:
                raise OperationError(
                    "path_denied", "Portable data must be inside the saved state's directory"
                ) from None
            for item in datasets:
                candidate = self.path(str(folder / item["filename"]), Path(item["path"]).suffix.lower())
                if digest(candidate) != item["sha256"]:
                    raise OperationError("stale_dataset", "Portable data must be a byte-identical dataset copy")
                item["path"] = str(candidate)
                relative_paths[item["name"]] = candidate.relative_to(output.parent).as_posix()

        def verify(staged):
            document = ElementTree.parse(staged).getroot()
            identifiers = {
                item.get("name"): int(item.get("id"))
                for item in document.findall(".//ProxyCollection[@name='sources']/Item")
            }
            for item in datasets:
                item["id"] = identifiers[item["name"]]
                if digest(Path(item["path"])) != item["sha256"]:
                    raise OperationError("stale_dataset", "Input dataset changed while saving native state")
                if folder is not None:
                    proxy = document.find(".//Proxy[@id='%s']" % item["id"])
                    for property_name in ("FileName", "FileNameInfo"):
                        values = [e.get("value") for e in proxy.findall("Property[@name='%s']/Element" % property_name)]
                        if values != [relative_paths[item["name"]]]:
                            raise OperationError(
                                "verification_failed", "Native state did not store relative reader paths"
                            )
            if folder is not None:
                parents = {child: parent for parent in document.iter() for child in parent}
                for element in document.iter():
                    for value in element.attrib.values():
                        parent = parents.get(element)
                        # '/' here is a root data-assembly selector, not a filesystem path.
                        if (
                            value == "/"
                            and parent is not None
                            and parent.tag == "Property"
                            and parent.get("name") == "BlockSelectors"
                        ):
                            continue
                        if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
                            raise OperationError(
                                "verification_failed", "Portable state still contains an absolute path"
                            )
                if str(self.root).encode() in staged.read_bytes():
                    raise OperationError("verification_failed", "Portable state still contains the workspace path")
            return {"xml_root": document.tag, "portable_relative_paths": folder is not None}

        original_cwd = os.getcwd()
        try:
            if folder is not None:
                # These are native FileName properties, never edits of serialized XML.
                # The owned pvpython main thread serializes all access to this temporary cwd.
                os.chdir(output.parent)
                for name, relative in relative_paths.items():
                    self.source(name).FileName = [relative]
                if self.inspect_pipeline()["sources"] != expected:
                    raise OperationError("verification_failed", "Portable reader rebind changed native data")
            result = self.publish(output, self.pv.SaveState, verify)
        finally:
            for name, original in original_paths.items():
                self.source(name).FileName = [original]
            os.chdir(original_cwd)
        if self.inspect_pipeline()["sources"] != expected:
            raise OperationError("verification_failed", "Original native reader state was not restored")
        self.saved_states[str(output)] = {"digest": result["sha256"], "sources": expected, "datasets": datasets}
        return {
            "artifact": result,
            "sources": expected,
            "datasets": [{key: value for key, value in item.items() if key != "id"} for item in datasets],
        }

    def _relocation_directory(self, value):
        folder = Path(value)
        folder = self.root / folder if not folder.is_absolute() else folder
        try:
            relative = folder.relative_to(self.root)
        except ValueError:
            raise OperationError("path_denied", "Data directory must remain inside the configured workspace") from None
        cursor = self.root
        for part in relative.parts:
            if part == "..":
                raise OperationError("path_denied", "Parent traversal is unsupported")
            cursor = cursor / part
            if cursor.is_symlink():
                raise OperationError("path_denied", "Symlink components are unsupported")
        if not folder.is_dir():
            raise OperationError("invalid_input", "Data directory must already exist")
        return folder.resolve()

    def reopen_state(self, path, data_directory=None):
        state = self.path(path, ".pvsm")
        state_hash = digest(state)
        record = self.saved_states.get(str(state))
        if record is None and data_directory is not None:
            # A relocated copy may be accepted only by a hash already generated by this
            # current owned session. A caller-provided manifest does not establish trust.
            matching = [entry for entry in self.saved_states.values() if entry["digest"] == state_hash]
            if matching and all(entry == matching[0] for entry in matching):
                record = matching[0]
        if record is None or record["digest"] != state_hash:
            raise OperationError(
                "untrusted_state", "Only unchanged native states saved by this running session may reopen"
            )
        folder = self._relocation_directory(data_directory) if data_directory is not None else None
        mappings, expected_paths = [], {}
        for item in record["datasets"]:
            candidate = folder / item["filename"] if folder is not None else Path(item["path"])
            candidate = self.path(str(candidate), Path(item["path"]).suffix.lower())
            if digest(candidate) != item["sha256"]:
                raise OperationError("stale_dataset", "State data must match the saved native dataset digest")
            expected_paths[item["name"]] = str(candidate)
            mappings.append({"name": item["name"], "id": item["id"], "FileName": [str(candidate)]})
        # Exact native filenames mappings avoid recursive directory searching or any
        # fallback to an old absolute path. The state bytes themselves stay unchanged.
        self.pv.ResetSession()
        self.view = None
        self.pv.LoadState(str(state), filenames=mappings)
        views = self.pv.GetViews()
        self.view = views[0] if views else None
        actual = self.inspect_pipeline()["sources"]
        if actual != record["sources"]:
            raise OperationError("verification_failed", "Native state reopened with different pipeline information")
        for name, expected_path in expected_paths.items():
            if list(self.source(name).FileName) != [expected_path]:
                raise OperationError("verification_failed", "Native reader did not use the verified dataset path")
        self.saved_states[str(state)] = record
        return {
            "sources": actual,
            "path": str(state),
            "data_directory": str(folder) if folder else None,
            "reader_paths": expected_paths,
            "relocation_verified": folder is not None,
        }

    def export_dataset(self, name, path):
        source = self.source(name)
        source.UpdatePipeline()
        expected = self.info(name, source)
        data_type = source.GetDataInformation().GetDataSetTypeAsString()
        suffix = {"vtkPolyData": ".vtp", "vtkImageData": ".vti"}.get(data_type, ".vtu")
        output = self.path(path, suffix, output=True)

        def verify(staged):
            reader = self._reader(staged)
            try:
                actual = self.info("verification", reader)
                for key in ("points", "cells", "bounds", "point_arrays"):
                    if actual[key] != expected[key]:
                        raise OperationError("verification_failed", "Dataset geometry round-trip differs")
                return {key: actual[key] for key in ("points", "cells", "bounds")}
            finally:
                self.pv.Delete(reader)

        result = self.publish(output, lambda p: self.pv.SaveData(p, proxy=source), verify)
        return {"artifact": result}

    def _reader(self, path, name=None):
        kwargs = {"FileName": [str(path)]}
        if name:
            kwargs["registrationName"] = name
        factory = {
            ".vtp": self.pv.XMLPolyDataReader,
            ".vtu": self.pv.XMLUnstructuredGridReader,
            ".vti": self.pv.XMLImageDataReader,
        }[path.suffix.lower()]
        return factory(**kwargs)

    def open_dataset(self, name, path):
        self.new_name(name)
        suffix = Path(path).suffix.lower()
        if suffix not in {".vtu", ".vtp", ".vti"}:
            raise OperationError("invalid_input", "Only single-file VTU, VTP and VTI datasets are supported")
        source_path = self.path(path, suffix)
        # Only XML single-file datasets; reject DTDs/entities and external piece references.
        raw = source_path.read_bytes()
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise OperationError("invalid_input", "DTD and entity declarations are unsupported")
        # VTK's appended binary payload is not XML. Inspect only its XML header.
        header = raw.split(b"<AppendedData", 1)[0]
        if b"<AppendedData" in raw:
            header += b"</VTKFile>"
        try:
            xml = ElementTree.fromstring(header)
        except ElementTree.ParseError:
            raise OperationError("invalid_dataset", "Dataset XML header is invalid") from None
        expected_type = {".vtu": "UnstructuredGrid", ".vtp": "PolyData", ".vti": "ImageData"}[suffix]
        if xml.tag != "VTKFile" or xml.get("type") != expected_type:
            raise OperationError("invalid_dataset", "Dataset type differs from its extension")
        if any("source" in key.lower() or "file" in key.lower() for element in xml.iter() for key in element.attrib):
            raise OperationError("invalid_input", "External dataset references are unsupported")
        reader = self._reader(source_path, name)
        try:
            actual = self.info(name, reader)
            if actual["points"] == 0:
                raise OperationError("invalid_dataset", "Dataset contains no points")
            return {"source": actual}
        except Exception:
            self.pv.Delete(reader)
            raise

    @staticmethod
    def _presentation_properties(proxy, properties):
        """Read existing properties only; never create a proxy or update the pipeline."""
        result = {}
        for key, (native, kind) in properties.items():
            prop = proxy.GetProperty(native)
            if prop is None:
                continue
            count = prop.GetNumberOfElements()
            if count > 4096:
                raise OperationError("resource_limit", "Presentation property exceeds its element limit")
            if kind != "vector" and count != 1:
                raise OperationError("verification_failed", "Presentation scalar property has unexpected cardinality")
            # Internal SM properties can exist without Python attribute accessors
            # (e.g. IndexedLookup in 5.13.2). Read the existing property directly.
            # Wrapped GetData preserves ArraySelectionProperty's [association, name].
            read_data = getattr(prop, "GetData", None)
            if read_data is not None:
                value = read_data()
            else:
                value = [prop.GetElement(i) for i in range(count)] if kind == "vector" else prop.GetElement(0)
            if kind == "vector":
                value = [] if value is None else list(value) if isinstance(value, (list, tuple)) else [value]
                if len(value) > 4096:
                    raise OperationError("resource_limit", "Presentation property exceeds its element limit")
            elif kind == "enum":
                value = prop.GetElement(0)
                # A raw vtkSMProperty has no Python EnumerationProperty conversion.
                domain = prop.FindDomain("vtkSMEnumerationDomain")
                if domain is not None and not isinstance(value, str):
                    for index in range(domain.GetNumberOfEntries()):
                        if domain.GetEntryValue(index) == value:
                            value = domain.GetEntryText(index)
                            break
            elif kind == "bool":
                value = bool(value)
            elif kind == "float":
                value = float(value)
            elif kind == "string":
                value = str(value)
                if len(value) > 4096:
                    raise OperationError("resource_limit", "Presentation text exceeds its length limit")
            result[key] = value
        return result

    def _palette(self, lookup):
        if lookup is None:
            return None
        result = self._presentation_properties(
            lookup,
            {
                "rgb_points": ("RGBPoints", "vector"),
                "color_space": ("ColorSpace", "enum"),
                "nan_color": ("NanColor", "vector"),
                "use_log_scale": ("UseLogScale", "bool"),
                "indexed_lookup": ("IndexedLookup", "bool"),
                "vector_mode": ("VectorMode", "enum"),
                "vector_component": ("VectorComponent", "float"),
                "below_range_color": ("BelowRangeColor", "vector"),
                "above_range_color": ("AboveRangeColor", "vector"),
                "use_below_range_color": ("UseBelowRangeColor", "bool"),
                "use_above_range_color": ("UseAboveRangeColor", "bool"),
            },
        )
        points = result.get("rgb_points", [])
        if points:
            if len(points) < 4 or len(points) % 4:
                raise OperationError("verification_failed", "Existing palette has invalid RGB control points")
            result["range"] = [points[0], points[-4]]
        return result

    def _bindings(self, representation, sources):
        prop = representation.GetProperty("Input")
        if prop is None:
            return []
        native = prop.SMProperty
        if native.GetNumberOfProxies() > 32:
            raise OperationError("resource_limit", "Representation input count exceeds its limit")
        bindings = []
        for i in range(native.GetNumberOfProxies()):
            source_proxy = native.GetProxy(i)
            names = sorted(name for (name, _), source in sources.items() if source.SMProxy == source_proxy)
            if not names:
                raise OperationError("verification_failed", "Representation input has no registered source name")
            bindings.append({"source_names": names, "port": native.GetOutputPortForConnection(i)})
        return bindings

    def inspect_presentation(self):
        """Inventory existing views and attached representations without creating anything."""
        sources = self.pv.GetSources()
        views = list(self.pv.GetViews())
        if len(sources) > 32 or len(views) > 16:
            raise OperationError("resource_limit", "Existing presentation exceeds bounded inventory limits")
        records = []
        for view in views:
            record = {"type": view.SMProxy.GetXMLName()}
            record.update(
                self._presentation_properties(
                    view,
                    {
                        "camera_position": ("CameraPosition", "vector"),
                        "camera_target": ("CameraFocalPoint", "vector"),
                        "camera_view_up": ("CameraViewUp", "vector"),
                        "camera_parallel_projection": ("CameraParallelProjection", "bool"),
                        "camera_parallel_scale": ("CameraParallelScale", "float"),
                        "camera_view_angle": ("CameraViewAngle", "float"),
                        "camera_clipping_range": ("CameraClippingRange", "vector"),
                        "view_size": ("ViewSize", "vector"),
                        "view_time": ("ViewTime", "float"),
                        "background": ("Background", "vector"),
                        "background2": ("Background2", "vector"),
                        "background_color_mode": ("BackgroundColorMode", "enum"),
                        "use_color_palette_for_background": ("UseColorPaletteForBackground", "bool"),
                        "show_orientation_axes": ("OrientationAxesVisibility", "bool"),
                    },
                )
            )
            record["unavailable_properties"] = [] if "camera_clipping_range" in record else ["camera_clipping_range"]
            reps = list(view.Representations)
            if len(reps) > 256:
                raise OperationError("resource_limit", "Existing representation count exceeds its limit")
            representations, scalar_bars = [], []
            for rep in reps:
                item = {"type": rep.SMProxy.GetXMLName()}
                item.update(self._presentation_properties(rep, {"visible": ("Visibility", "bool")}))
                lookup = rep.LookupTable if rep.GetProperty("LookupTable") is not None else None
                if item["type"] == "ScalarBarWidgetRepresentation":
                    item.update(
                        self._presentation_properties(
                            rep,
                            {
                                "title": ("Title", "string"),
                                "component_title": ("ComponentTitle", "string"),
                                "position": ("Position", "vector"),
                                "orientation": ("Orientation", "enum"),
                                "window_location": ("WindowLocation", "enum"),
                                "length": ("ScalarBarLength", "float"),
                                "thickness": ("ScalarBarThickness", "float"),
                                "title_color": ("TitleColor", "vector"),
                                "label_color": ("LabelColor", "vector"),
                                "title_font_size": ("TitleFontSize", "float"),
                                "label_font_size": ("LabelFontSize", "float"),
                                "automatic_label_format": ("AutomaticLabelFormat", "bool"),
                                "label_format": ("LabelFormat", "string"),
                                "range_label_format": ("RangeLabelFormat", "string"),
                                "draw_tick_labels": ("DrawTickLabels", "bool"),
                                "use_custom_labels": ("UseCustomLabels", "bool"),
                                "custom_labels": ("CustomLabels", "vector"),
                            },
                        )
                    )
                    item["palette"] = self._palette(lookup)
                    item["bindings"] = sorted(
                        [
                            binding
                            for candidate in reps
                            if candidate.GetProperty("Input") is not None
                            and candidate.GetProperty("LookupTable") is not None
                            and candidate.LookupTable == lookup
                            for binding in self._bindings(candidate, sources)
                        ],
                        key=lambda x: json.dumps(x, sort_keys=True),
                    )
                    scalar_bars.append(item)
                else:
                    item["bindings"] = self._bindings(rep, sources)
                    item.update(
                        self._presentation_properties(
                            rep,
                            {
                                "representation": ("Representation", "enum"),
                                "color_array": ("ColorArrayName", "vector"),
                                "diffuse_color": ("DiffuseColor", "vector"),
                                "ambient_color": ("AmbientColor", "vector"),
                                "ambient": ("Ambient", "float"),
                                "diffuse": ("Diffuse", "float"),
                                "specular": ("Specular", "float"),
                                "specular_power": ("SpecularPower", "float"),
                                "opacity": ("Opacity", "float"),
                                "line_width": ("LineWidth", "float"),
                                "point_size": ("PointSize", "float"),
                                "lighting": ("Lighting", "bool"),
                            },
                        )
                    )
                    item["palette"] = self._palette(lookup)
                    representations.append(item)
            record["representations"] = sorted(representations, key=lambda x: json.dumps(x, sort_keys=True))
            record["scalar_bars"] = sorted(scalar_bars, key=lambda x: json.dumps(x, sort_keys=True))
            record["visible_bindings"] = [
                b for r in record["representations"] if r.get("visible") for b in r["bindings"]
            ]
            records.append((record, view == self.view))
        try:
            records.sort(key=lambda x: json.dumps(x[0], sort_keys=True, allow_nan=False))
            result = {
                "views": [r for r, _ in records],
                "current_view_index": next((i for i, (_, current) in enumerate(records) if current), None),
            }
            if len(json.dumps(result, allow_nan=False).encode()) > 512 * 1024:
                raise OperationError("resource_limit", "Presentation inventory exceeds its byte limit")
        except (ValueError, TypeError):
            raise OperationError("verification_failed", "Presentation is not bounded finite JSON") from None
        return result

    @staticmethod
    def _verify_png(path, dimensions):
        data = path.read_bytes()
        actual = [int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")]
        if data[:8] != b"\x89PNG\r\n\x1a\n" or actual != dimensions:
            raise OperationError("verification_failed", "PNG header or dimensions differ")
        from vtkmodules.vtkIOImage import vtkPNGReader

        reader = vtkPNGReader()
        reader.SetFileName(str(path))
        reader.Update()
        pixels = reader.GetOutput()
        if list(pixels.GetDimensions()) != dimensions + [1]:
            raise OperationError("verification_failed", "Native PNG decode dimensions differ")
        scalars = pixels.GetPointData().GetScalars()
        if scalars is None or scalars.GetNumberOfComponents() not in (3, 4):
            raise OperationError("verification_failed", "Native PNG has no RGB pixel data")
        ranges = [list(scalars.GetRange(channel)) for channel in range(3)]
        if all(low == high for low, high in ranges):
            raise OperationError("verification_failed", "Native PNG contains only a flat color")
        return {"dimensions": actual, "pixel_ranges": ranges}

    def capture_current_view(self, path):
        validate("capture_current_view", {"path": path})
        output = self.path(path, ".png", output=True)
        if not os.environ.get("DISPLAY"):
            raise OperationError("render_unavailable", "This adapter requires DISPLAY for this ParaView rendering lane")
        before = self.inspect_presentation()
        index = before["current_view_index"]
        if index is None or before["views"][index]["type"] != "RenderView":
            raise OperationError("view_not_found", "No configured current render view exists")
        dimensions = before["views"][index].get("view_size", [])
        if len(dimensions) != 2 or any(type(n) is not int or not 64 <= n <= 2048 for n in dimensions):
            raise OperationError("resource_limit", "Current view size must be within [64, 2048] per axis")

        def verify(staged):
            after = self.inspect_presentation()
            if after != before:
                raise OperationError("presentation_changed", "Native capture changed presentation; no image published")
            return {**self._verify_png(staged, dimensions), "presentation": after, "presentation_unchanged": True}

        # No Render/ResetCamera/Show/configuration helpers. A native first-render reset
        # is surfaced by the verifier and is never silently repaired or published.
        return {"artifact": self.publish(output, lambda p: self.pv.SaveScreenshot(p, self.view), verify)}

    @staticmethod
    def _apply_legend_controls(scalar_bar, controls):
        # Only explicitly supplied controls are written; omission keeps the existing native behavior.
        for key, native in {
            "scalar_bar_title": "Title",
            "scalar_bar_length": "ScalarBarLength",
            "scalar_bar_thickness": "ScalarBarThickness",
            "scalar_bar_title_font_size": "TitleFontSize",
            "scalar_bar_label_font_size": "LabelFontSize",
        }.items():
            if key in controls:
                setattr(scalar_bar, native, controls[key])
        if "scalar_bar_position" in controls:
            scalar_bar.WindowLocation = "Any Location"
            scalar_bar.Orientation = "Vertical"
            scalar_bar.Position = controls["scalar_bar_position"]

    def _legend_control_readback(self, scalar_bar, controls):
        properties = {
            "scalar_bar_title": ("Title", "string"),
            "scalar_bar_position": ("Position", "vector"),
            "scalar_bar_length": ("ScalarBarLength", "float"),
            "scalar_bar_thickness": ("ScalarBarThickness", "float"),
            "scalar_bar_title_font_size": ("TitleFontSize", "float"),
            "scalar_bar_label_font_size": ("LabelFontSize", "float"),
        }
        selected = {key: value for key, value in properties.items() if key in controls}
        if "scalar_bar_position" in controls:
            selected.update(
                scalar_bar_orientation=("Orientation", "enum"), scalar_bar_window_location=("WindowLocation", "enum")
            )
        actual = self._presentation_properties(scalar_bar, selected)
        if set(actual) != set(selected):
            raise OperationError("verification_failed", "Native legend control property is unavailable")
        return actual

    def render_preview(
        self,
        name,
        path,
        width=800,
        height=600,
        scalar=None,
        color_range=None,
        preset="Cool to Warm (Extended)",
        camera_position=None,
        camera_target=None,
        background=None,
        solid_color=None,
        show_scalar_bar=True,
        show_orientation_axes=False,
        camera_parallel_scale=None,
        camera_view_angle=30,
        camera_view_up=None,
        ambient=0,
        diffuse=1,
        specular=0,
        specular_power=100,
        line_width=None,
        scalar_bar_title=None,
        scalar_bar_position=None,
        scalar_bar_length=None,
        scalar_bar_thickness=None,
        scalar_bar_title_font_size=None,
        scalar_bar_label_font_size=None,
    ):
        optional_controls = {
            "line_width": line_width,
            "scalar_bar_title": scalar_bar_title,
            "scalar_bar_position": scalar_bar_position,
            "scalar_bar_length": scalar_bar_length,
            "scalar_bar_thickness": scalar_bar_thickness,
            "scalar_bar_title_font_size": scalar_bar_title_font_size,
            "scalar_bar_label_font_size": scalar_bar_label_font_size,
        }
        controls = {key: value for key, value in optional_controls.items() if value is not None}
        # Validate new controls before source/view/visibility/legend mutations, including direct host calls.
        validate(
            "render_preview",
            {"name": name, "path": path, **controls, **({"scalar": scalar} if scalar is not None else {})},
        )
        if not os.environ.get("DISPLAY"):
            raise OperationError("render_unavailable", "This adapter requires DISPLAY for this ParaView rendering lane")
        source = self.source(name)
        output = self.path(path, ".png", output=True)
        if scalar is not None:
            self.scalar(source, scalar)
        if self.view is None:
            self.view = self.pv.CreateView("RenderView")
        view = self.view
        for proxy in self.pv.GetSources().values():
            self.pv.Hide(proxy, view)
        self.pv.HideUnusedScalarBars(view)
        view.ViewSize = [width, height]
        view.OrientationAxesVisibility = int(show_orientation_axes)
        view.UseColorPaletteForBackground = 0
        view.Background = background or [0.04, 0.06, 0.10]
        display = self.pv.Show(source, view)
        display.Representation = "Surface"
        display.Ambient = ambient
        display.Diffuse = diffuse
        display.Specular = specular
        display.SpecularPower = specular_power
        if line_width is not None:
            display.LineWidth = line_width
        if scalar is not None:
            self.pv.ColorBy(display, ("POINTS", scalar))
            lookup = self.pv.GetColorTransferFunction(scalar)
            if not lookup.ApplyPreset(preset, True):
                raise OperationError("invalid_input", "This ParaView build does not provide the selected preset")
            if color_range is not None:
                lookup.RescaleTransferFunction(*color_range)
            scalar_bar = self.pv.GetScalarBar(lookup, view)
            display.SetScalarBarVisibility(view, show_scalar_bar)
            scalar_bar.Visibility = int(show_scalar_bar)
            self._apply_legend_controls(scalar_bar, controls)
        else:
            self.pv.ColorBy(display, None)
            display.DiffuseColor = solid_color or [0.25, 0.70, 0.90]
            display.AmbientColor = solid_color or [0.25, 0.70, 0.90]
            self.pv.HideUnusedScalarBars(view)
        # Complete ParaView's first-render camera initialization before applying explicit camera values.
        self.pv.Render(view)
        self.pv.ResetCamera(view)
        if camera_position is not None:
            view.CameraPosition = camera_position
        if camera_target is not None:
            view.CameraFocalPoint = camera_target
        direction = [a - b for a, b in zip(view.CameraPosition, view.CameraFocalPoint)]
        if sum(v * v for v in direction) < 1e-12:
            raise OperationError("invalid_input", "Camera position and target must differ")
        up = camera_view_up or ([0, 1, 0] if abs(direction[0]) + abs(direction[1]) < 1e-8 else [0, 0, 1])
        cross = [
            direction[1] * up[2] - direction[2] * up[1],
            direction[2] * up[0] - direction[0] * up[2],
            direction[0] * up[1] - direction[1] * up[0],
        ]
        if sum(v * v for v in cross) < 1e-12:
            raise OperationError("invalid_input", "Camera view-up must not be parallel to its direction")
        view.CameraViewUp = up
        view.CameraParallelProjection = int(camera_parallel_scale is not None)
        if camera_parallel_scale is not None:
            view.CameraParallelScale = camera_parallel_scale
        view.CameraViewAngle = camera_view_angle

        def visual_readback():
            actual = {
                "camera_position": list(view.CameraPosition),
                "camera_target": list(view.CameraFocalPoint),
                "camera_view_up": list(view.CameraViewUp),
                "background": list(view.Background),
                "view_size": list(view.ViewSize),
                "representation": display.GetProperty("Representation").GetElement(0),
                "scalar": scalar,
                "show_orientation_axes": bool(view.OrientationAxesVisibility),
                "show_scalar_bar": bool(scalar_bar.Visibility) if scalar is not None else False,
                "camera_parallel_projection": bool(view.CameraParallelProjection),
                "camera_parallel_scale": float(view.CameraParallelScale),
                "camera_view_angle": float(view.CameraViewAngle),
                "ambient": float(display.Ambient),
                "diffuse": float(display.Diffuse),
                "specular": float(display.Specular),
                "specular_power": float(display.SpecularPower),
            }
            expected = {"background": background or [0.04, 0.06, 0.10], "view_size": [width, height]}
            expected.update(
                {
                    "camera_parallel_projection": camera_parallel_scale is not None,
                    "show_orientation_axes": show_orientation_axes,
                    "camera_view_angle": camera_view_angle,
                    "camera_view_up": up,
                    "ambient": ambient,
                    "diffuse": diffuse,
                    "specular": specular,
                    "specular_power": specular_power,
                    "show_scalar_bar": show_scalar_bar if scalar is not None else False,
                }
            )
            if line_width is not None:
                line_readback = self._presentation_properties(display, {"line_width": ("LineWidth", "float")})
                if "line_width" not in line_readback:
                    raise OperationError("verification_failed", "Native line width property is unavailable")
                actual.update(line_readback)
                expected["line_width"] = line_width
            if SCALAR_BAR_CONTROLS.intersection(controls):
                legend_readback = self._legend_control_readback(scalar_bar, controls)
                actual.update(legend_readback)
                expected.update({key: value for key, value in controls.items() if key in SCALAR_BAR_CONTROLS})
                if scalar_bar_position is not None:
                    expected.update(scalar_bar_orientation="Vertical", scalar_bar_window_location="Any Location")
            if camera_parallel_scale is not None:
                expected["camera_parallel_scale"] = camera_parallel_scale
            if camera_position is not None:
                expected["camera_position"] = camera_position
            if camera_target is not None:
                expected["camera_target"] = camera_target
            if any(actual[key] != value for key, value in expected.items()):
                raise OperationError(
                    "verification_failed",
                    "Native view/camera readback differs: %s"
                    % {
                        key: {"expected": value, "actual": actual[key]}
                        for key, value in expected.items()
                        if actual[key] != value
                    },
                )
            if actual["representation"] != "Surface":
                raise OperationError("verification_failed", "Native representation readback differs")
            if scalar is not None:
                if list(display.ColorArrayName) != ["POINTS", scalar]:
                    raise OperationError("verification_failed", "Native scalar coloring readback differs")
                points = list(lookup.RGBPoints)
                actual["color_range"] = [points[0], points[-4]]
                actual["color_control_points"] = points
                actual["preset"] = preset
                if color_range is not None and actual["color_range"] != color_range:
                    raise OperationError("verification_failed", "Native color range readback differs")
            else:
                actual["diffuse_color"] = list(display.DiffuseColor)
                if actual["diffuse_color"] != (solid_color or [0.25, 0.70, 0.90]):
                    raise OperationError("verification_failed", "Native surface color readback differs")
            return actual

        visual_readback()

        def verify(p):
            data = p.read_bytes()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                raise OperationError("verification_failed", "Render is not a PNG")
            actual = [int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")]
            if actual != [width, height]:
                raise OperationError(
                    "verification_failed",
                    "Render dimensions differ: expected %s, actual %s" % ([width, height], actual),
                )
            from vtkmodules.vtkIOImage import vtkPNGReader

            reader = vtkPNGReader()
            reader.SetFileName(str(p))
            reader.Update()
            pixels = reader.GetOutput()
            if list(pixels.GetDimensions()) != [width, height, 1]:
                raise OperationError("verification_failed", "Native PNG decode dimensions differ")
            scalars = pixels.GetPointData().GetScalars()
            if scalars is None or scalars.GetNumberOfComponents() not in (3, 4):
                raise OperationError("verification_failed", "Native PNG has no RGB pixel data")
            ranges = [list(scalars.GetRange(channel)) for channel in range(3)]
            if all(low == high for low, high in ranges):
                raise OperationError("verification_failed", "Native PNG contains only a flat color")
            return {"dimensions": actual, "pixel_ranges": ranges, "view": visual_readback()}

        return {
            "artifact": self.publish(
                output, lambda p: self.pv.SaveScreenshot(p, view, ImageResolution=[width, height]), verify
            )
        }

    def call(self, operation, params):
        if threading.get_ident() != self.thread_id:
            raise OperationError("wrong_thread", "Host API requires the owning main thread")
        validate(operation, params)
        return getattr(self, operation)(**params)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    # Reserve the original stdout for protocol frames; host print output goes to stderr.
    wire = os.fdopen(os.dup(1), "w", buffering=1)
    incoming = os.fdopen(os.dup(0), "rb")
    sys.stdout = sys.stderr
    host = Host(sys.argv[1])
    wire.write(json.dumps({"ready": True, "pid": os.getpid(), "version": host.version, "protocol": 1}) + "\n")
    wire.flush()
    while True:
        line = incoming.readline(1024 * 1024 + 1)
        if not line:
            break
        if len(line) > 1024 * 1024 or not line.endswith(b"\n"):
            break
        request = {}
        try:
            request = json.loads(line)
            if (
                not isinstance(request, dict)
                or set(request) != {"id", "job_id", "operation", "params"}
                or not isinstance(request["id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", request["id"])
                or (
                    request["job_id"] is not None
                    and (not isinstance(request["job_id"], str) or not 1 <= len(request["job_id"]) <= 256)
                )
            ):
                break
            context = host.call(request["operation"], request["params"])
            response = {"id": request["id"], "job_id": request.get("job_id"), "result": context}
        except Exception as error:
            if not isinstance(request, dict) or "id" not in request or "job_id" not in request:
                break
            code = getattr(error, "code", None)
            if not isinstance(code, str) or not 1 <= len(code) <= 100:
                code = "host_error"
            response = {
                "id": request.get("id"),
                "job_id": request.get("job_id"),
                "error": {"code": code, "message": str(error)[:500]},
            }
        try:
            encoded = (json.dumps(response, allow_nan=False) + "\n").encode("utf-8")
        except (TypeError, ValueError):
            encoded = b""
        if not encoded or len(encoded) > 1024 * 1024:
            response = {
                "id": request["id"],
                "job_id": request["job_id"],
                "error": {"code": "response_limit", "message": "Host result was not bounded finite JSON"},
            }
            encoded = (json.dumps(response) + "\n").encode("utf-8")
        wire.write(encoded.decode("utf-8"))
        wire.flush()


if __name__ == "__main__":
    main()
