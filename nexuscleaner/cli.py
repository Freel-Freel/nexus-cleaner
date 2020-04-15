#!/usr/bin/env python3

import os
import click
import logging


logger = logging.getLogger("nexuscleaner")


@click.command()
@click.option("--host", help="nexus host", required=True)
@click.option("--port", help="nexus port", default="443")
@click.option("--base-url", help="nexus base url", required=True)
@click.option("--debug", help="print debug info", is_flag=True)
@click.option("-r", "--release", help="Nexus repository, e.g. Release", required=True)
@click.option("-c", "--check", help="Check artifacts from local file against artifacts_rules", is_flag=True)
@click.option("-l", "--artlist", help="Create nexus_artifacts_list.txt", is_flag=True)

def cli(host, port, base_url, debug, release, check, artlist):
    if debug:
        logging.basicConfig(format='%(name)s::%(levelname)s:: %(message)s', level=logging.DEBUG)
        logger.debug("host: %s", host)
        logger.debug("port: %s", port)
        logger.debug("base url: %s", base_url)
        logger.debug("release: %s", release)
        logger.debug("check artifacts list: %s", check)
        logger.debug("artifacts list: %s", artlist)
    else:
        logger.setLevel(logging.ERROR)


if __name__ == '__main__':
    cli()
