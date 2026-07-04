# ADR-0001: Record architecture decisions

**Status:** Accepted · **Date:** 2026-07-04

## Context

This project is built by multiple AI agents over months. Decisions made once must not be silently re-litigated by an agent that lacks context.

## Decision

We record every binding architectural decision as an ADR in `docs/adr/`, numbered sequentially, using this format (Status/Date, Context, Decision, Consequences). ADRs are immutable once accepted; changing a decision requires a new ADR that explicitly supersedes the old one. Only the Claude tier (or the human owner) may accept or supersede an ADR.

## Consequences

Agents can treat any accepted ADR as ground truth. A PR that contradicts an accepted ADR is rejected regardless of code quality. Contract changes (`packages/contracts`) always require an ADR.
