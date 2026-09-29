import pytest

from ros2_typed_launch import config as cfg


def test_deep_merge():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    over = {"a": {"y": 3, "z": 4}, "b": 2}
    assert cfg.deep_merge(base, over) == {"a": {"x": 1, "y": 3, "z": 4}, "b": 2}
    assert base == {"a": {"x": 1, "y": 2}, "b": 1}  # inputs untouched
    assert cfg.deep_merge({"t": [1, 2]}, {"t": [3]}) == {"t": [3]}  # lists replace


def test_nest_and_lookup():
    nested = cfg.nest({"a.b": 1, "a.c": 2, "d": 3})
    assert nested == {"a": {"b": 1, "c": 2}, "d": 3}
    assert cfg.lookup(nested, ("a", "c")) == (True, 2)
    assert cfg.lookup(nested, ("a", "zzz")) == (False, None)
    assert cfg.lookup(nested, ("d", "x")) == (False, None)


def test_load_config_file(write):
    assert cfg.load_config_file(write("c.yaml", "a: 1\nb: {c: 2}\n", raw=True)) == {
        "a": 1,
        "b": {"c": 2},
    }
    assert cfg.load_config_file(write("empty.yaml", "", raw=True)) == {}


@pytest.mark.parametrize("text", ["- 1\n- 2\n", "just a string\n", "a: [unclosed\n"])
def test_load_config_file_rejects_bad_files(write, text):
    with pytest.raises(cfg.ConfigFileError):
        cfg.load_config_file(write("bad.yaml", text, raw=True))


def test_load_config_file_missing():
    with pytest.raises(cfg.ConfigFileError, match="cannot read"):
        cfg.load_config_file("/nonexistent/file.yaml")


def test_resolve_config_ref_plain_path_wins(write):
    path = write("x.yaml", "a: 1\n")
    assert cfg.resolve_config_ref(path) == path


def test_resolve_config_ref_package_syntax(tmp_path, monkeypatch):
    share = tmp_path / "share"
    (share / "config").mkdir(parents=True)
    (share / "config" / "site.yaml").write_text("a: 1\n")
    monkeypatch.setattr(cfg, "_package_share_dir", lambda pkg: str(share))

    assert cfg.resolve_config_ref("mypkg:config/site.yaml") == str(
        share / "config" / "site.yaml"
    )
    assert cfg.resolve_config_ref("mypkg:/config/site.yaml") == str(
        share / "config" / "site.yaml"
    )
    with pytest.raises(cfg.ConfigFileError, match="no file 'config/nope.yaml'"):
        cfg.resolve_config_ref("mypkg:config/nope.yaml")


def test_resolve_config_ref_existing_file_beats_package_reading(tmp_path, monkeypatch):
    # A file literally named "pkg:thing.yaml" in the cwd is used as a path.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pkg:thing.yaml").write_text("a: 1\n")
    monkeypatch.setattr(
        cfg, "_package_share_dir", lambda pkg: pytest.fail("must not look up package")
    )
    assert cfg.resolve_config_ref("pkg:thing.yaml") == "pkg:thing.yaml"


def test_resolve_config_ref_non_reference_passes_through():
    assert cfg.resolve_config_ref("no_such_dir/file.yaml") == "no_such_dir/file.yaml"
