ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

.PHONY: deploy deploy-host deploy-runner register-runner test

deploy: deploy-host

deploy-host:
	./scripts/deploy-host.sh

deploy-runner:
	./scripts/deploy-runner.sh

register-runner:
	./scripts/register-runner.sh

test:
	PYTHONPATH=$(ROOT)/src python3 -m unittest discover -s $(ROOT)/tests -v
