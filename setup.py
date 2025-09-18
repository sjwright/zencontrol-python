"""
Setup script for zencontrol-python library.
"""

from setuptools import setup, find_packages
import os

# Read the README file
def read_readme():
    with open("README.md", "r", encoding="utf-8") as fh:
        return fh.read()

# Read requirements
def read_requirements():
    with open("requirements.txt", "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="zencontrol",
    version="0.0.0",
    author="Simon Wright",
    description="This is an implementation of the Zencontrol TPI Advanced protocol, written in Python.",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/sjwright/zencontrol-python",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU Lesser General Public License v2.1 (LGPLv2.1)",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Home Automation",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.11",
    install_requires=read_requirements(),
    extras_require={
        "dev": [
            "pytest>=6.0",
            "pytest-asyncio>=0.18.0",
            "black>=22.0",
            "flake8>=4.0",
            "mypy>=0.950",
        ],
        "mqtt": [
            "aiomqtt>=2.1.0",
            "PyYAML>=6.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "zencontrol-mqtt=examples.mqtt_bridge:main",
        ],
    },
    include_package_data=True,
    package_data={
        "zencontrol": ["py.typed"],
    },
    zip_safe=False,
)
