#!/bin/bash
# Sourced (not run) by the 'best-practices' stage -- 'source:' on a
# provider: custom section. A 'cmd:' would run in an isolated subprocess and
# this export would die with it; sourced, it reaches every later stage and
# the final command.

export PYTEST_ADDOPTS="-v -s"
export CMAKE_GENERATOR="Ninja"
