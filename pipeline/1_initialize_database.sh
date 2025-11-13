#!/bin/sh

createdb placetrace
psql placetrace < schema.sql

