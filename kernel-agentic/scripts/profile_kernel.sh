#!/usr/bin/env bash
rocprof -o run.prof ./kernel.out
rocprof --stats run.prof  # pretty print
