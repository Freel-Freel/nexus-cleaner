from setuptools import setup


setup(
    name='nexuscleaner',
    author='',
    description='nexus cleaner',
    version='0.1',
    packages=['nexuscleaner'],
    install_requires=[
        'Click',
        'requests'
    ],
    entry_points='''
        [console_scripts]
        sdoup=nexuscleaner.cli:cli
    ''',
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX",
    ]
)
