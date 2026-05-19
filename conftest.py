def pytest_addoption(parser):
    parser.addoption("--mm-path", action="store", default=None,
                     help="Path to Micro-Manager application directory")
    parser.addoption("--demo-config", action="store", default=None,
                     help="Path to MMConfig_demo.cfg")
