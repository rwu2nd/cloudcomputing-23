How to run tests in this folder:

NOTE: pod recovery test disabled by default

1. open new terminal at project root:
   cd ...\cloudcomputing-23

2. run:
   docker compose up -d --build

3. check services are running:
   docker compose ps

4. install required dependencies:
   python -m pip install pytest requests

5. run all test files in the test folder:
   python -m pytest tests/ -v

sample output in terminal:
tests/test_init.py::test_is_backend_live PASSED [ 33%]
tests/test_init.py::test_is_backend_ready PASSED [ 66%]
tests/test_init.py::test_ping_endpoint PASSED [100%]

========================================== 3 passed in 6.27s ===========================================
