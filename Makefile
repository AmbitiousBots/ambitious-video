.PHONY: tlc check

tlc:
	bash spec/check.sh

check:
	bash verify/run
