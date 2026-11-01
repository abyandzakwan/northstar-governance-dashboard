from portal.domain import catalog

data = catalog()
assert data["rules"]
assert data["profiles"]["production"]["threshold"] == 95
assert data["profiles"]["production"]["max_critical"] == 0
print(
    f"Validated {len(data['rules'])} rules, {len(data['frameworks'])} frameworks; version {data['version']}"
)
