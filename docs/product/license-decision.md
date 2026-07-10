# Licence Decision

Status: **blocking before public repository publication**.

An unlicensed public repository is source-available, not open source. Select and
commit the licence before accepting external contributions.

## Leading options

### AGPL-3.0-or-later

Advantages:

- Improvements to network-hosted versions must remain available under the same
  licence.
- Fits a self-hosted service whose value is delivered through a web interface.
- Discourages closed hosted forks that do not contribute improvements.

Costs:

- Some companies and downstream projects prohibit AGPL dependencies.
- Integration and contribution reviews need greater licence awareness.

### Apache-2.0

Advantages:

- Permissive commercial and community adoption.
- Explicit patent grant.
- Widely accepted by companies and distributions.

Costs:

- Hosted or commercial forks may remain closed.
- Community improvements are not guaranteed to return upstream.

## Decision criteria

- Is reciprocal publication of hosted modifications a core project value?
- Is maximum compatibility and adoption more important than reciprocity?
- Should future plugins have the same licence or merely compatible licences?
- Are dependencies and copied prototype components compatible with the choice?

Record the final decision as an ADR, add the exact licence text at repository
root, and update README, contribution, and dependency policies.
