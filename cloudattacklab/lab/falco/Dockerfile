# Falco for the lab: the pinned upstream image plus the lab's one rule file.
# The rule file is copied in (not bind-mounted) because the backend runs
# `docker compose` from inside its own container, where a relative bind path
# would resolve on the host, not in this folder.
FROM falcosecurity/falco:0.45.2
COPY cloudattacklab_rules.yaml /etc/falco/rules.d/cloudattacklab_rules.yaml
