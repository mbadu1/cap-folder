# Fixed Substack compatibility examples

All people, publications, URLs, and text in these fixtures are synthetic. They contain no corpus records, credentials, or network dependencies.

`input.json` contains nine API-like records. `expected.json` contains seven exact expected retained records, independently specified from the collection contract; the out-of-window records are excluded. The expected strings, counts, identifiers, dates, roles, access behavior and schema are fixed files. Tests never regenerate expected outputs from the production parser.

The examples cover the inclusive study boundaries, Unicode and paragraph normalization, HTML/script/style removal, numeric and fallback identifiers, coauthors and publication membership, paid and unknown access, free-unlock restrictions, missing bodies, foreign-language acquisition, and observed zero engagement. Short and non-English text may occur in raw acquisition; these fixtures do not label it eligible for final analysis. A fallback post identifier is not proof of a stable native identity.

`manifest.json` binds these examples, the original `4d8d6e5` collector file hashes and the actual frozen team batch. The normalized expected-output SHA-256 is:

```text
2987d6b4406fa38ef2ac7700b726f468afff3c576943465d3505a057b16e3ded
```

Compare normalized record JSON, not SQLite file bytes or export archive bytes: observation times, database layouts and compression can differ across machines. A failing check is a reason to inspect the discrepancy, not regenerate this reference to make it pass.
