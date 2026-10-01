"""Optional native decoder; a missing compiler leaves the Python path usable."""
from setuptools import Extension, setup
from Cython.Build import cythonize

setup(ext_modules=cythonize(
    [Extension("tablescan_local._beam_native", ["src/tablescan_local/_beam_native.pyx"], optional=True)],
    build_dir="build/cython",
    compiler_directives={"language_level": 3},
))
