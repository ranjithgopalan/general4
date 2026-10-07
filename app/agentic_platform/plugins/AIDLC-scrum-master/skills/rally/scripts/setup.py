"""Setup script for Rally API CLI."""

from setuptools import setup, find_packages

# Read requirements
with open('requirements.txt') as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
    name='rally-api-cli',
    version='1.0.0',
    description='Dynamic CLI for Rally API',
    author='Christopher Le',
    packages=find_packages(),
    py_modules=['cli'],
    install_requires=requirements,
    entry_points={
        'console_scripts': [
            'rally=cli:main',
        ],
    },
    python_requires='>=3.8',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
    ],
)
