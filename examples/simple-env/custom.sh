#!/bin/bash
# Wired via 'source:' on the 'set-vars' stage in denver.toml (provider:
# custom) -- not the global hooks: mechanism. Sourced, so these exports
# persist into every later stage and the final command. Set whatever you
# like here -- this is just a dummy demo.

export MYVAR=1
export FOO=2
export BAR=3
