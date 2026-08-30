enum V8DiscourseActType {
    ANSWER_NOW @alias("answer_now")
    WANTS_TRIAGE @alias("wants_triage")
    CORRECTION @alias("correction")
    CLARIFICATION_REQUEST @alias("clarification_request")
    FACT_STATEMENT @alias("fact_statement")
    QUESTION @alias("question")
    REPORT_CONTEXT @alias("report_context")
}

enum V8UserStatementType {
    REPORTS @alias("reports")
    DENIES @alias("denies")
    REPORTS_NORMAL @alias("reports_normal")
    REPORTS_ABNORMAL @alias("reports_abnormal")
    UNCERTAIN @alias("uncertain")
    HISTORICAL @alias("historical")
    HYPOTHETICAL @alias("hypothetical")
    ASKS @alias("asks")
    CORRECTS @alias("corrects")
}

enum V8CoarseType {
    SYMPTOM @alias("symptom")
    STATE @alias("state")
    ACTION @alias("action")
    EXPOSURE @alias("exposure")
    FOOD @alias("food")
    MEDICATION @alias("medication")
    TIME @alias("time")
    MEASUREMENT @alias("measurement")
    CONTEXT @alias("context")
}

class V8MacroDiscourseAct {
    unit_id string
    act_type V8DiscourseActType
    evidence_span_ids string[]
    confidence float
}

class V8MacroClaim {
    unit_id string
    claim_id string
    statement_type V8UserStatementType
    coarse_type V8CoarseType
    support_span_ids string[]
    target_span_ids string[]
    relation_span_ids string[]
    subject_span_ids string[]
    action_agent_span_ids string[]
    action_recipient_span_ids string[]
    experiencer_span_ids string[]
    object_span_ids string[]
    temporal_span_ids string[]
    measurement_span_ids string[]
    confidence float
}

class V8MacroSemanticOutput {
    schema_version "v8-macro-raw"
    acts V8MacroDiscourseAct[]
    claims V8MacroClaim[]
}

function ExtractV8Macro(messages_json: string) -> V8MacroSemanticOutput {
    client V8LiteLLM
    prompt #"
        You are an input-preprocessing shadow experiment adapter.
        Read the complete serialized OpenAI-compatible messages.
        Return only the typed structure requested by BAML.
        Never invent a span_id. Evidence and semantic bindings may only use
        span_id values present in the input. Never return a free-form quote.
        Do not diagnose, assess medical risk, provide advice, or select routing.
        Multiple discourse acts may be true at once. When a value is uncertain,
        return an empty list rather than fabricating an item.

        {{ messages_json }}

        {{ ctx.output_format }}
    "#
}
