package trivy

# Only these inspected Python base-image layers (linux/amd64 and linux/arm64)
# contain the pip SBOM that reports setuptools 70.3.0. Reassess after a base
# image update; a different layer must not inherit these exceptions.
inspected_layers := {
    "sha256:777bfdd276b1339ba90876c7b9bb0f4c04cdd38049a494d1250afef3ae17c392",
    "sha256:e27bc4d9e2fd237f93910c7082a1e5117a8afc3f7ee41b624917c232af9c28c1",
}

# pip vendors only pkg_resources from setuptools, not the affected
# PackageIndex, sdist builder, or Windows wheel installation implementation.
inspected_findings := {
    "CVE-2025-47273",
    "CVE-2026-59890",
    "GHSA-xmj3-9gf7-h52w",
}

default ignore = false

ignore {
    input.Type == "vulnerability"
    input.PkgIdentifier.BOMRef == "pkg:pypi/setuptools@70.3.0"
    inspected_layers[input.Layer.DiffID]
    inspected_findings[input.VulnerabilityID]
}
