"""Owner AI interview (#120): question progress, intent reviews and first draft generation.

    app.interview.routes        the 12 endpoints (router)
    app.interview.flow          question -> answer -> Jev state machine, task inputs
    app.interview.tasks         task handlers (INITIAL_QUESTION ... DRAFT_GENERATION)
    app.interview.drafting      completion snapshot and draft composition
    app.interview.common        loading, lock order, phase projection, API bodies
    app.interview.content       review content <-> AI StructureSnapshot
    app.interview.question_set  the fixed required question set (seeded by migration 0040)
"""
