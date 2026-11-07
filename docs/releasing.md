# CI and releasing

`.github/workflows/ci.yml`, on every push and PR:

| Job | What |
|---|---|
| `libtmux-easy` | `python3-libtmux-easy`'s `.deb`: downloaded from a `nasa-jpl/libtmux_easy` release (`LIBTMUX_EASY`, default `v0.4.0`), or built from a branch when it is not a `v*` tag |
| `test` | `ros:jazzy-ros-base`: that `.deb`, `rosdep install`, `colcon build`, quick tests, then `colcon test` (live tests too) |
| `deb` | `bloom-generate rosdebian` + `fakeroot debian/rules binary` -> `ros-jazzy-lte-launch_*.deb`; installed and smoke-tested |
| `release` | On a `v*` tag matching `package.xml`'s version: a GitHub release with both `.deb`s |

Run by hand (Actions -> CI -> Run workflow) to pick another libtmux_easy branch or tag.

## Installing a release

```bash
sudo apt install ./python3-libtmux-easy_*.deb ./ros-jazzy-lte-launch_*.deb
```

## rosdep

`python3-libtmux-easy` is not in rosdistro; `rosdep/lte_launch.yaml` maps the key. With its
`.deb` installed, rosdep sees it as satisfied:

```bash
echo "yaml file://$PWD/rosdep/lte_launch.yaml" | sudo tee /etc/ros/rosdep/sources.list.d/50-lte-launch.list
rosdep update && rosdep install --from-paths . --ignore-src -y
```

## Making a release

1. Bump `<version>` in `package.xml`; if it needs a newer libtmux_easy, bump `LIBTMUX_EASY` in `ci.yml`.
2. `git tag v<version> && git push origin v<version>`.
