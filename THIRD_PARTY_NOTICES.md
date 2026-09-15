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

The repository cites published sources by DOI or URL. The current source tree
redistributes no article content — no PDFs, figures, tables, or quoted passages.

Two article PDFs were committed in `f6cc25a` and removed in `3db1b88`, and they
remain in this repository's Git history, which a full clone fetches: the Nature
Protocols article `10.1038/nprot.2017.024`, whose publisher holds copyright, and
the Creative Commons-licensed review article `10.1038/s43586-021-00038-x`.
Neither is part of the BSD-licensed work, neither is covered by `LICENSE`, and
nothing here grants permission to reuse them. Obtain either article from its
publisher through the DOI above; anyone redistributing this repository's history
carries those files and should treat them accordingly.

Where a packaged skill in `microclaw/skills/` says it is "condensed from" a
source, the condensation is Microclaw's own expression of that source's factual
guidance and is covered by `LICENSE`. Each such skill credits its source in its
own `## Sources` block. The complete set:

| Source | Cited by | Identifier |
| --- | --- | --- |
| Schnitzbauer et al., "Super-resolution microscopy with DNA-PAINT", *Nat Protoc* 2017 | `dna-paint` | `10.1038/nprot.2017.024` |
| Lelek et al., "Single-molecule localization microscopy", *Nat Rev Methods Primers* 2021 | `dna-paint`, `smlm` | `10.1038/s43586-021-00038-x` |
| Jonkman et al., "Tutorial: guidance for quantitative confocal microscopy", *Nat Protoc* 2020 | `fluorescence-microscopy` | `10.1038/s41596-020-0313-9` |
| "The Microscope Optical Train", Nikon MicroscopyU | `optical-paths` | <https://www.microscopyu.com/microscopy-basics/components> |
