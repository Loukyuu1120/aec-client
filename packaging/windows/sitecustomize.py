import os

if os.environ.get('AEC_STUDIO_INSTALLED') == '1':
    from web.runtime_env import activate_configured_runtime
    activate_configured_runtime()
