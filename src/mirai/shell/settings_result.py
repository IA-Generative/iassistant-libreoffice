"""Enregistrement des champs validés dans le dialogue Paramètres."""


def apply_settings_result(job, result):
    if "endpoint" in result and result["endpoint"].startswith("http"):
        job.set_config("llm_base_urls", result["endpoint"])

    if "api_key" in result:
        job.set_config("llm_api_tokens", result["api_key"])
        job._log("Settings saved: llm_api_tokens updated")

    if "model" in result:
        job.set_config("llm_default_models", result["model"])

