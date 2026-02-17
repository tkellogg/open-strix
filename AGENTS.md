# Python
* Use `uv` always to access anything python, `uv add` to install, `uv run` to run *anything*
* Write frequent but clear and terse comments
* Generally don't catch exceptions. In a web context, let the top-level handler catch it. Only use try/except if you're in a context that doesn't handle errors. It's sometimes fine to catch exceptions, in places where that's the most expedient way to gain information.
* Focus on writing clear code that's easy to follow.

# Quality
* Use higher level tests to ensure that code works. Focus tests on things that are likely to break, like the boundaries between systems.
* Use types extensively to maintain contracts and enforce logic. 
* Use monitoring to observe how the agent works.
