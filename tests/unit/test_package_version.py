"""Numéro de version : l'écriture du Device Management (X.Y.Z, X.Y.Z-rc.N,
X.Y.Z-dev.N) et celle que LibreOffice sait ranger dans description.xml."""

import itertools
import os
import subprocess
import sys

import pytest

from src.mirai import package_version

MODULE = os.path.abspath(package_version.__file__)


def libreoffice_compare(left, right):
    """`dp_misc::compareVersions` de LibreOffice (dp_version.cxx), transcrit :
    éléments séparés par des points, zéros de tête retirés, le plus long
    l'emporte, puis l'ordre des caractères ; un élément absent est vide."""
    a, b = left.split("."), right.split(".")
    for i in range(max(len(a), len(b))):
        e1 = a[i].lstrip("0") if i < len(a) else ""
        e2 = b[i].lstrip("0") if i < len(b) else ""
        if (len(e1), e1) != (len(e2), e2):
            return -1 if (len(e1), e1) < (len(e2), e2) else 1
    return 0


def precedence(version):
    """Rang d'un numéro dans l'ordre des versions : une pré-version précède sa
    version finale, un essai précède une candidate."""
    base, _, suffix = version.partition("-")
    rank, number = 3, 0
    if suffix:
        kind, _, number = suffix.partition(".")
        rank = {"dev": 1, "rc": 2}[kind]
    return tuple(int(part) for part in base.split(".")) + (rank, int(number))


@pytest.mark.parametrize("version, native", [
    ("0.4.0", "0.4.0"),
    ("0.4.0-rc.12", "0.3.99999.2.12"),
    ("0.4.0-dev.7", "0.3.99999.1.7"),
    ("0.3.1-rc.5", "0.3.0.2.5"),
    ("1.0.0-rc.3", "0.99999.99999.2.3"),
    ("2.5.9-dev.140", "2.5.8.1.140"),
])
def test_a_version_has_one_native_writing_and_reads_back(version, native):
    assert package_version.native_version(version) == native
    assert package_version.dm_version(native) == version


def test_libreoffice_misorders_a_suffixed_version():
    """Ce pourquoi le suffixe n'est pas inscrit tel quel dans description.xml."""
    assert libreoffice_compare("0.4.0-rc.12", "0.4.0") > 0
    assert libreoffice_compare("0.4.0-rc.12", "0.4.1") > 0


def test_native_writings_are_ordered_by_libreoffice_like_the_versions():
    bases = ["0.3.0", "0.3.1", "0.3.12", "0.4.0", "0.10.0", "1.0.0", "1.0.1", "1.2.0"]
    versions = list(bases)
    for base, kind, number in itertools.product(bases, ("dev", "rc"), (1, 2, 9, 10, 250)):
        versions.append(f"{base}-{kind}.{number}")
    for left, right in itertools.combinations(versions, 2):
        expected = (precedence(left) > precedence(right)) - (precedence(left) < precedence(right))
        got = libreoffice_compare(package_version.native_version(left),
                                  package_version.native_version(right))
        assert got == expected, (left, right)


@pytest.mark.parametrize("version", ["0.0.1.0.32", "0.3.0", "", "1.9.0", "0.3.0.3.4", "abc"])
def test_what_is_not_a_prerelease_is_left_alone(version):
    assert package_version.dm_version(version) == version
    assert package_version.native_version(version) == version


def test_versions_read_from_libreoffice_are_trimmed():
    assert package_version.dm_version(" 0.3.99999.2.12 ") == "0.4.0-rc.12"
    assert package_version.dm_version(" 0.0.1.0.32 ") == "0.0.1.0.32"


@pytest.mark.parametrize("version, kind", [
    ("0.4.0-rc.12", "rc"), ("0.4.0-dev.3", "dev"), ("0.4.0", ""), ("0.0.1.0.32", ""), ("", ""),
])
def test_prerelease_kind(version, kind):
    assert package_version.prerelease_kind(version) == kind


@pytest.mark.parametrize("version", ["0.4.0", "0.4.0-rc.1", "12.0.3-dev.99"])
def test_versions_a_package_can_carry(version):
    assert package_version.is_valid(version)


@pytest.mark.parametrize("version", [
    "0.4", "0.4.0-beta.1", "0.4.0-rc", "0.4.0-rc.x", "v0.4.0", "0.99999.0", "0.0.0-rc.1",
    "0.4.0.1", "",
])
def test_versions_a_package_cannot_carry(version):
    assert not package_version.is_valid(version)


def test_the_build_asks_the_module_for_the_native_writing():
    ok = subprocess.run([sys.executable, MODULE, "native", "0.4.0-rc.12"],
                        capture_output=True, text=True)
    assert (ok.returncode, ok.stdout.strip()) == (0, "0.3.99999.2.12")
    refused = subprocess.run([sys.executable, MODULE, "native", "0.4.0-beta.1"],
                             capture_output=True, text=True)
    assert refused.returncode == 1 and refused.stdout == ""
