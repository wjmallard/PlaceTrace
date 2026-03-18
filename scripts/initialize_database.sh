#!/bin/sh

createdb placetrace
psql placetrace < sql/schema.sql

