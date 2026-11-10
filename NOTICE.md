# Notice

## GDTF fixture files

`lighting_visualizer/library/gdtf/` contains fixture files downloaded from [GDTF Share](https://gdtf-share.com) through
its public API, with the account of the add-on author. `index.json` records, for each file, the manufacturer, fixture
name, revision, GDTF version, GDTF Share revision id and whether it was uploaded by the manufacturer (`Manuf.`) or by a
community user (`User`). [docs/fixtures.md](docs/fixtures.md) lists them all.

- The files, their DMX data and 3D models belong to their uploaders and respective manufacturers. Product names and
  trademarks belong to their owners; no affiliation or endorsement is implied.
- Files marked as community uploads are not verified by the manufacturer and may contain errors.
- GDTF Share asks applications to keep cached files current. The bundled files are a snapshot taken when the release
  was made; `tools/gdtf_share.py` fetches newer revisions. If you are an uploader or manufacturer and want a file
  removed or updated, please open an issue.

**Powered by GDTF.** GDTF and MVR are open standards ([gdtf.eu](https://gdtf.eu)).

## psutil

`lighting_visualizer/wheels/` contains unmodified wheels of [psutil](https://github.com/giampaolo/psutil), BSD 3-Clause
licence, Copyright (c) 2009, Jay Loden, Dave Daeschler, Giampaolo Rodola.
