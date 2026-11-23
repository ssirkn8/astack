import importlib
import keyword

PACKAGE_NAME = "ds_project"


def test_package_name_is_a_non_keyword_identifier() -> None:
    assert PACKAGE_NAME.isidentifier()
    assert not keyword.iskeyword(PACKAGE_NAME)


def test_package_imports() -> None:
    assert importlib.import_module(PACKAGE_NAME) is not None
