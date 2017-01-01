PYTHONFILES := $(wildcard *.py)

FILES=transcode_h264.py mythutils.py undelete_recordings.py

PYLINT_OPTS=--reports=n --disable=I
PEP8_OPTS=--max-line-length=100 --ignore=E701

all: pep8 pylint

.PHONY: pylint
pylint:
	pylint ${FILES}

.PHONY: pep8
pep8:
	pep8 ${PEP8_OPTS} ${FILES}
