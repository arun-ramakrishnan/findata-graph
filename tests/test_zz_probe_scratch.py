def test_probe2(unit_client):
    probes = [
        ("GET", "/api/graph/suggestions"),
        ("GET", "/api/graph/suggestions?method=jaccard"),
        ("GET", "/api/graph/edition_companies?edition=Asian%20Paints%20NMDC%20IndiGo"),
        (
            "GET",
            "/api/graph/edition_companies?edition=A%20Strange%20Quarter%20With%20Very%20Real%20Cracks",
        ),
        ("GET", "/api/graph/similar/Asian%20Paints%20NMDC%20IndiGo.md"),
        ("GET", "/api/graph/neighbors/Banking"),
        ("GET", "/api/graph/metrics/louvain_community?top=5"),
    ]
    for method, url in probes:
        r = unit_client.open(url, method=method)
        b = r.get_json(silent=True)
        keys = sorted(b)[:12] if isinstance(b, dict) else repr(b)[:80]
        print(f"\n### {method} {url}\n  status={r.status_code} keys={keys}")
        if isinstance(b, dict) and url.endswith("neighbors/Banking"):
            print("   full:", {k: (str(v)[:40]) for k, v in b.items()})
