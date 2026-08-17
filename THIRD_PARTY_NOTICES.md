# Third-Party Notices

Microclaw's own source code and documentation are licensed under the BSD
3-Clause license in `LICENSE`. Dependencies are separate works and remain
subject to their own licenses. They are installed from the Python package
index; their source code is not vendored in this repository.

The table below records the licenses declared by Microclaw's direct runtime
and optional dependencies. Exact dependency versions are selected at install
time, so distributors should also preserve the license files shipped in the
installed distributions and review notices for the versions they distribute.

| Dependency | Use | Declared license |
| --- | --- | --- |
| anthropic | runtime | MIT |
| pycromanager | runtime | BSD 3-Clause |
| ndstorage | runtime | BSD 3-Clause |
| tifffile | runtime | BSD 3-Clause |
| NumPy | runtime | BSD 3-Clause |
| PyYAML | runtime | MIT |
| SciPy | runtime | BSD 3-Clause; binary distributions may include components under other compatible licenses |
| scikit-image | runtime | BSD 3-Clause; includes separately attributed BSD and MIT components |
| Pillow | runtime | HPND |
| FastAPI | optional `serve` and test | MIT |
| Uvicorn | optional `serve` | BSD 3-Clause |
| keyring | optional `serve` | MIT |
| pytest | test only | MIT |
| pytest-mock | test only | MIT |
| HTTPX | test only | BSD 3-Clause |
| setuptools | build only | MIT |

Transitive dependencies are not enumerated because they vary by platform and
resolver output. A binary or environment redistributor must collect and comply
with the notices of the complete resolved dependency set.

## Research references

The repository cites research articles by DOI, but does not redistribute the
article PDFs. In particular, the previously included Nature Protocols article
`10.1038/nprot.2017.024` and the Creative Commons-licensed review article
`10.1038/s43586-021-00038-x` are references, not parts of the BSD-licensed work.
