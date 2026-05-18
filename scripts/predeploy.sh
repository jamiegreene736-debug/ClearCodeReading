#!/bin/sh
set -eu

echo "Running Clear Code Reading shared migrations..."
python manage.py migrate_schemas --shared --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Seeding Reading Survey question bank..."
python -u manage.py seed_reading_survey_questions

echo "Seeding demo login credentials..."
python -u manage.py seed_demo_login

echo "Clear Code Reading pre-deploy complete."
