#!/bin/sh
set -eu

echo "Running Clear Code Reading shared migrations..."
python manage.py migrate_schemas --shared --noinput

if [ "${ENABLE_DEMO_ACCESS:-0}" = "1" ]; then
  echo "Seeding isolated demo environment..."
  python -u manage.py seed_admin_demo_data
else
  python -u manage.py retire_demo_accounts
fi

if { [ -n "${ELEVENLABS_API_KEY:-}" ] || [ -n "${XI_API_KEY:-}" ]; } && [ -n "${ELEVENLABS_VOICE_ID:-}" ]; then
  echo "Generating missing ElevenLabs assessment audio..."
  python -u manage.py generate_assessment_audio --no-fail
else
  echo "Skipping ElevenLabs audio generation; ELEVENLABS_API_KEY/XI_API_KEY and ELEVENLABS_VOICE_ID are not both set."
fi

echo "Clear Code Reading pre-deploy complete."
