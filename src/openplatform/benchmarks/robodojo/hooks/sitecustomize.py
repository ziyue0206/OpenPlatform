"""Install after the pinned upstream module loads, without importing Isaac early."""

import os
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import PathFinder
from importlib.util import spec_from_file_location
from pathlib import Path


class _Loader(Loader):
    def __init__(self, original):
        self.original = original

    def create_module(self, spec):
        return self.original.create_module(spec)

    def exec_module(self, module):
        self.original.exec_module(module)
        from openplatform.benchmarks.robodojo.bridge import install_from_environment

        install_from_environment(module)


class _Finder(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        location = {
            "openplatform": os.environ.get("OPENPLATFORM_RD_CORE_DIR"),
            "openplatform_sdk": os.environ.get("OPENPLATFORM_RD_SDK_DIR"),
        }.get(fullname)
        if location:
            if fullname == "openplatform_sdk":
                installed = PathFinder.find_spec(fullname, path, target)
                if installed is not None:
                    return installed
            package = Path(location)
            return spec_from_file_location(
                fullname, package / "__init__.py", submodule_search_locations=[location]
            )
        if fullname != "src.eval_client.eval_env":
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        spec.loader = _Loader(spec.loader)
        return spec


if os.environ.get("OPENPLATFORM_RD_MODE") in {"official", "bridge"} and sys.argv[0].endswith(
    "src/eval_client/main.py"
):
    sys.meta_path.insert(0, _Finder())
