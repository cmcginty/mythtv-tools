PYTHONFILES := $(wildcard *.py)

PYLINT_OPTS=--reports=n --disable=I
PEP8_OPTS=--max-line-length=100

all: pep8 pylint

.PHONY: pylint
pylint:
	pylint *.py

.PHONY: pep8
pep8:
	pep8 ${PEP8_OPTS} *.py
