import importlib
import pathlib
import sys

import pytest

from ros2_typed_launch import loader


def test_resolve_by_path(sample_launch, fake_package):
    typed = loader.resolve(sample_launch)
    assert typed.config_type.__name__ == "Cfg"

    assert loader.resolve(sample_launch) is loader.resolve(sample_launch)

    for target, match in [
        ((), "no launch given"),
        (("a", "b", "c"), "expected a path"),
        (("/no/such/file_launch.py",), "is not a file"),
    ]:
        with pytest.raises(loader.LaunchLookupError, match=match):
            loader.resolve(*target)

    # main_launch.py does `from .child_launch import ...`; loaded by path (no
    # parent package) that raises Python's ImportError, which we should turn
    # into a hint to use the <package> <module> form instead -- this is
    # exactly the pitfall of running `tlaunch some_launch.py` from inside an
    # installed package's launch/ directory.
    tpkg = importlib.import_module(fake_package)
    path = str(pathlib.Path(tpkg.__file__).parent / "main_launch.py")
    with pytest.raises(
        loader.LaunchLookupError,
        match="no parent package.*tlaunch ros2_typed_launch",
    ):
        loader.resolve(path)


def test_launch_file_names(fake_package):
    # For tab completion: every *_launch.py stem in the package, sorted.
    assert loader.launch_file_names(fake_package) == [
        "child_launch",
        "main_launch",
        "nested_launch",
        "not_a_launch",
    ]

    assert loader.launch_file_names("no_such_package") == []


def test_resolve_by_package(fake_package):
    # Full filename, found by searching the package -- the common case (like
    # `ros2 launch <package> <file>` searches share/). ".py" is required: the
    # bare stem alone is rejected (see test_resolve_errors).
    typed = loader.resolve(fake_package, "main_launch.py")
    assert typed.config_type.__name__ == "MainConfig"

    # The dotted form (launch.nested_launch) and the on-disk slash form
    # (launch/nested_launch.py, leading "/" optional) must resolve to the
    # identical module.
    dotted = loader.resolve(fake_package, "launch.nested_launch")
    assert dotted.config_type.__name__ == "ChildConfig"
    for spelling in ("launch/nested_launch.py", "/launch/nested_launch.py"):
        assert loader.resolve(fake_package, spelling) is dotted
    # The bare filename (no path), found by searching the package, resolves
    # to the same module too.
    assert loader.resolve(fake_package, "nested_launch.py") is dotted

    # The point of module-based resolution: no separate loading path, so a
    # launch reached via resolve() and via `import` are the same object.
    via_loader = loader.resolve(fake_package, "child_launch.py")
    imported = importlib.import_module(f"{fake_package}.child_launch")
    assert via_loader is imported.child

    assert loader.load_launch(fake_package, "child_launch.py") is loader.resolve(
        fake_package, "child_launch.py"
    )

    # main_launch.py does `from .child_launch import ChildConfig, child` and
    # includes it; if that relative import failed, loading would have raised.
    typed = loader.resolve(fake_package, "main_launch.py")
    from launch.actions import GroupAction

    [group] = typed.build(typed.config_type(n=7))
    assert isinstance(group, GroupAction)
    assert "child-7" in group.get_sub_entities()


def test_ambiguous_names_and_import_cycles_are_clear_errors(tmp_path, monkeypatch):
    pkg = tmp_path / "ambig"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "dup_launch.py").write_text("")
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "dup_launch.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with pytest.raises(
            loader.LaunchLookupError,
            match=r"'dup_launch\.py' is ambiguous.*dup_launch.*sub\.dup_launch",
        ):
            loader.resolve("ambig", "dup_launch.py")
    finally:
        for name in list(sys.modules):
            if name == "ambig" or name.startswith("ambig."):
                del sys.modules[name]

    # Verified behaviour, not guessed: a genuine import cycle is caught by
    # Python's own import machinery with a clear error, no special handling
    # needed on our side.
    pkg = tmp_path / "cyc"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a_launch.py").write_text("from .b_launch import x\n")
    (pkg / "b_launch.py").write_text("from .a_launch import x\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with pytest.raises(loader.LaunchLookupError, match="failed to (import|load)"):
            loader.resolve("cyc", "a_launch.py")
    finally:
        for name in list(sys.modules):
            if name == "cyc" or name.startswith("cyc."):
                del sys.modules[name]


def test_resolve_errors(fake_package):
    with pytest.raises(
        loader.LaunchLookupError, match="package 'no_such_pkg' not found"
    ):
        loader.resolve("no_such_pkg", "x.py")
    with pytest.raises(
        loader.LaunchLookupError,
        match="no launch file named 'no_such_module.py' in package 'tpkg'",
    ):
        loader.resolve(fake_package, "no_such_module.py")  # full filename, searched
    with pytest.raises(
        loader.LaunchLookupError,
        match="no launch module 'launch.no_such_module' in package 'tpkg'",
    ):
        loader.resolve(fake_package, "launch.no_such_module")  # explicit dotted path

    # A bare stem with no ".py" and no "." is neither a valid filename nor a
    # dotted module path -- rejected with a hint, not silently searched.
    with pytest.raises(
        loader.LaunchLookupError,
        match=r"give the full filename, including '\.py'",
    ):
        loader.resolve(fake_package, "main_launch")

    with pytest.raises(loader.LaunchLookupError, match="not a typed launch file"):
        loader.resolve(fake_package, "not_a_launch.py")
