client<llm> V8LiteLLM {
    provider openai-generic
    retry_policy V8Bounded
    options {
        base_url env.INPUT_PREPROCESSING_LITELLM_BASE_URL
        api_key env.INPUT_PREPROCESSING_LITELLM_API_KEY
        model "qwen-plus"
    }
}

retry_policy V8Bounded {
    max_retries 0
    strategy {
        type constant_delay
        delay_ms 0
    }
}
