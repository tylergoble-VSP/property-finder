"""agentfinder — find luxury listing agents around a point, for a designer's outreach.

An annex to property-finder. It reuses core's seams and never the other way round: core
does not import anything here, its command line gains nothing, its config mentions nothing.
The isolation is at the *import* layer; both packages share one SQLite file, and the annex
owns migration versions 100+ (core owns 1–99).

The one thing this package exists to do that core cannot: recover *who is selling* a luxury
home. The feed has no agent field, so identity is recovered from Google's index of the
syndicated MLS "Listed by" block, verified against the Texas licence register, and carried
with an honest confidence tier — CONFIRMED / INFERRED / UNRESOLVED — exactly as
`propertyfinder.dataquality` recovers a builder. A guess is never dressed as a fact.
"""
