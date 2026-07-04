.PHONY: setup lint typecheck test dev-up

setup:
	uv sync
	pnpm install

lint:
	scripts/lint

typecheck:
	scripts/typecheck

test:
	scripts/test

dev-up:
	scripts/dev-up
