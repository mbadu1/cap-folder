# Two-person compatibility contract

The synthetic `input.json` and independently written `expected.json` are byte-for-byte copies of the v1 reference. The golden normalized output stays `2987d6b4406fa38ef2ac7700b726f468afff3c576943465d3505a057b16e3ded`. No parser behavior or expected result was changed for this release.

This new manifest pins the reviewed two-person orchestration and assignment batch `2026-09-28-team-v2`. Its `reference_commit` identifies the predecessor containing the unchanged parser/fixtures; the new release commit is supplied separately in the handoff. The old v1 fixture manifest and batch remain immutable for provenance. The expanded tests cover two-shard merging, migration of exact checkpoint records, stop/pause preservation, and refusal of mismatched owners or existing destination work.

Do not regenerate these fingerprints to force a failing check to pass. These are synthetic software checks, not evidence of historical source completeness or final sample eligibility.
