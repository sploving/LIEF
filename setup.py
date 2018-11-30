import os
import re
import sys
import platform
import subprocess
import glob
import setuptools
import pathlib
from pkg_resources import Distribution, get_distribution
from setuptools import setup, Extension
from setuptools import Extension
from setuptools.command.build_ext import build_ext, copy_file
from distutils import log

from distutils.version import LooseVersion

MIN_SETUPTOOLS_VERSION = "31.0.0"
assert (LooseVersion(setuptools.__version__) >= LooseVersion(MIN_SETUPTOOLS_VERSION)), "LIEF requires a setuptools version '{}' or higher (pip install setuptools --upgrade)".format(MIN_SETUPTOOLS_VERSION)

CURRENT_DIR  = pathlib.Path(__file__).parent
PACKAGE_NAME = "lief"

class LiefDistribution(setuptools.Distribution):
    global_options = setuptools.Distribution.global_options + [
        ('lief-test', None, 'Build and make tests'),
        ('ninja', None, 'Use Ninja as build system'),
        ]

    def __init__(self, attrs=None):
        self.lief_test = False
        self.ninja     = False
        super().__init__(attrs)


class Module(Extension):
    def __init__(self, name, sourcedir='', *args, **kwargs):
        Extension.__init__(self, name, sources=[])
        self.sourcedir = os.path.abspath(os.path.join(CURRENT_DIR))


class BuildLibrary(build_ext):
    def run(self):
        try:
            out = subprocess.check_output(['cmake', '--version'])
        except OSError:
            raise RuntimeError("CMake must be installed to build the following extensions: " +
                               ", ".join(e.name for e in self.extensions))

        #if platform.system() == "Windows":
        #    cmake_version = LooseVersion(re.search(r'version\s*([\d.]+)', out.decode()).group(1))
        #    if cmake_version < '3.1.0':
        #        raise RuntimeError("CMake >= 3.1.0 is required on Windows")
        for ext in self.extensions:
            self.build_extension(ext)
        self.copy_extensions_to_source()

    @staticmethod
    def has_ninja():
        try:
            subprocess.check_call(['ninja', '--version'])
            return True
        except Exception as e:
            return False


    def build_extension(self, ext):
        if self.distribution.lief_test:
            log.info("LIEF tests enabled!")
        fullname = self.get_ext_fullname(ext.name)
        filename = self.get_ext_filename(fullname)

        jobs = self.parallel if self.parallel else 1


        source_dir                     = ext.sourcedir
        build_temp                     = self.build_temp
        extdir                         = os.path.abspath(os.path.dirname(self.get_ext_fullpath(ext.name)))
        cmake_library_output_directory = os.path.abspath(os.path.dirname(build_temp))
        cfg                            = 'Debug' if self.debug else 'Release'
        is64                           = sys.maxsize > 2**32

        cmake_args = [
            '-DCMAKE_LIBRARY_OUTPUT_DIRECTORY={}'.format(cmake_library_output_directory),
            '-DPYTHON_EXECUTABLE={}'.format(sys.executable),
        ]

        if self.distribution.lief_test:
            cmake_args += ["-DLIEF_TESTS=on"]

        build_args = ['--config', cfg]

        if platform.system() == "Windows":
            cmake_args += ['-DCMAKE_LIBRARY_OUTPUT_DIRECTORY_{}={}'.format(cfg.upper(), cmake_library_output_directory)]
            cmake_args += ['-DLIEF_USE_CRT_RELEASE=MT']
            cmake_args += ['-A', 'x64'] if is64 else ['-A', 'x86']
            build_args += ['--', '/m']
        else:
            cmake_args += ['-DCMAKE_BUILD_TYPE={}'.format(cfg)]

        env = os.environ.copy()

        if not os.path.exists(self.build_temp):
            os.makedirs(self.build_temp)

        build_with_ninja = False
        if self.has_ninja() and self.distribution.ninja:
            cmake_args += ["-G", "Ninja"]
            build_with_ninja = True


        # 1. Configure
        configure_cmd = ['cmake', ext.sourcedir] + cmake_args
        subprocess.check_call(configure_cmd, cwd=self.build_temp, env=env)

        # 2. Build
        binding_target = "pyLIEF"
        if platform.system() == "Windows":
            subprocess.check_call(['cmake', '--build', '.', '--target', binding_target] + build_args, cwd=self.build_temp)
        else:
            if build_with_ninja:
                if self.distribution.lief_test:
                    subprocess.check_call(['ninja', "lief_samples"], cwd=self.build_temp)
                    subprocess.check_call(configure_cmd, cwd=self.build_temp)
                    subprocess.check_call(['ninja'], cwd=self.build_temp)
                    subprocess.check_call(['ninja', "check-lief"], cwd=self.build_temp)
                else:
                    subprocess.check_call(['ninja', binding_target], cwd=self.build_temp)
            else:
                log.info("Using {} jobs".format(jobs))
                if self.distribution.lief_test:
                    subprocess.check_call(['make', '-j{}'.format(jobs), "lief_samples"], cwd=self.build_temp)
                    subprocess.check_call(configure_cmd, cwd=self.build_temp)
                    subprocess.check_call(['make', '-j{}'.format(jobs), "all"], cwd=self.build_temp)
                    subprocess.check_call(['make', '-j{}'.format(jobs), "check-lief"], cwd=self.build_temp)
                else:
                    subprocess.check_call(['make', '-j{}'.format(jobs), binding_target], cwd=self.build_temp)

        pycosmiq_dst  = os.path.join(self.build_lib, self.get_ext_filename(self.get_ext_fullname(ext.name)))

        libsuffix = pycosmiq_dst.split(".")[-1]

        pycosmiq_path = os.path.join(cmake_library_output_directory, "{}.{}".format(PACKAGE_NAME, libsuffix))
        if not os.path.exists(self.build_lib):
            os.makedirs(self.build_lib)

        copy_file(
                pycosmiq_path, pycosmiq_dst, verbose=self.verbose,
                dry_run=self.dry_run)


# From setuptools-git-version
command       = 'git describe --tags --long --dirty'
is_tagged_cmd = 'git tag --list --points-at=HEAD'
fmt           = '{tag}.dev0'
fmt_tagged    = '{tag}'

def format_version(version, fmt=fmt):
    parts = version.split('-')
    assert len(parts) in (3, 4)
    dirty = len(parts) == 4
    tag, count, sha = parts[:3]
    if count == '0' and not dirty:
        return tag
    return fmt.format(tag=tag, gitsha=sha.lstrip('g'))


def get_git_version(is_tagged):
    git_version = subprocess.check_output(command.split()).decode('utf-8').strip()
    if is_tagged:
        return format_version(version=git_version, fmt=fmt_tagged)
    return format_version(version=git_version, fmt=fmt)

def check_if_tagged():
    output = subprocess.check_output(is_tagged_cmd.split()).decode('utf-8').strip()
    return output != ""

def get_pkg_info_version(pkg_info_file):
    dist_info = Distribution.from_filename(CURRENT_DIR / "{}.egg-info".format(PACKAGE_NAME))
    pkg = get_distribution('lief')
    return pkg.version


def get_version():
    version   = "0.0.0"
    pkg_info  = CURRENT_DIR / "{}.egg-info".format(PACKAGE_NAME) / "PKG-INFO"
    git_dir   = CURRENT_DIR / ".git"
    if git_dir.is_dir():
        is_tagged = False
        try:
            is_tagged = check_if_tagged()
        except:
            is_tagged = False

        try:
            return get_git_version(is_tagged)
        except:
            pass

    if pkg_info.is_file():
        return get_pkg_info_version(pkg_info)



version = get_version()
cmdclass = {
    'build_ext': BuildLibrary,
}

setup(
    distclass=LiefDistribution,
    ext_modules=[Module(PACKAGE_NAME)],
    cmdclass=cmdclass,
    version=version
)
