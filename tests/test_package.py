import vmd


def test_package_exposes_version():
    assert isinstance(vmd.__version__, str)
    assert vmd.__version__ != ""
