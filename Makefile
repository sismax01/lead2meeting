.PHONY: run seed reset scan test clean

run:
	python3 -m lead2meeting serve --host 127.0.0.1 --port 8000

seed:
	python3 -m lead2meeting seed

reset:
	python3 -m lead2meeting seed --reset

scan:
	python3 -m lead2meeting scan

test:
	python3 -m unittest discover -s tests -v

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -f lead2meeting.db
