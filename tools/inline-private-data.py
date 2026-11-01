"""Refresh only the recipe's marked inline initializer; no build/image dependency."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
compose = ROOT / 'runtipi/docker-compose.yml'
source = (ROOT / 'prepare_private_data.py').read_text()
text = compose.read_text()
start = text.index('  # BEGIN PRIVATE DATA INITIALIZATION\n')
end = text.index('  # END PRIVATE DATA INITIALIZATION\n', start)
end += len('  # END PRIVATE DATA INITIALIZATION\n')
app = 'wizard-chatgpt' if 'id: wizard-chatgpt\n' in (ROOT / 'wizard.md').read_text() else 'wizard-claude'
recipe_root = '/data' if app == 'wizard-chatgpt' else '/claude'
image = 'python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4'
host = '${APP_DATA_DIR}/data' + ('' if recipe_root == '/data' else '/claude')
code = ''.join(('          ' + line if line else '') + '\n' for line in source.splitlines())
block = f'''  # BEGIN PRIVATE DATA INITIALIZATION
  # Explicit image/recipe runtime identity: 1000:1000, never manager UID guessing.
  {app}-data-init:
    image: {image}
    x-runtipi:
      wizard_grants: false
    user: "0:0"
    network_mode: none
    read_only: true
    cap_drop: [ALL]
    cap_add: [CHOWN]
    security_opt: [no-new-privileges:true]
    restart: "no"
    volumes:
      - {host}:{recipe_root}
    command:
      - python3
      - -c
      - &private_data_code |
{code}      - prepare
  {app}-data-validate:
    image: {image}
    x-runtipi:
      wizard_grants: false
    user: "1000:1000"
    network_mode: none
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    restart: "no"
    depends_on:
      {app}-data-init:
        condition: service_completed_successfully
    volumes:
      - {host}:{recipe_root}:ro
    command: [python3, -c, *private_data_code, validate]
  # END PRIVATE DATA INITIALIZATION
'''
compose.write_text(text[:start] + block + text[end:])
