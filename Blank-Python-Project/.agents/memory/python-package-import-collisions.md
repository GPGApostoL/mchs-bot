---
name: Python package/import-name collisions
description: A package-management pitfall when a Python distribution name differs from its import module name.
---

When installing a Python library whose distribution name differs from its import module, the package helper may add a second distribution matching the import name. That can shadow or remove files shared by the intended library.

**Why:** The conflicting distributions can install into the same import namespace, leaving the intended package unusable.

**How to apply:** Check the generated project dependency list and test the import after installation. If the helper infers the wrong package from source imports, install the distribution before adding those imports, then verify only the intended distribution remains.
