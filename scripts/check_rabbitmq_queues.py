#!/usr/bin/python3

# Copyright (C) 2011, 2012, 2014 Canonical
# All Rights Reserved
# Author: Liam Young, Jacek Nykis

from collections import defaultdict
from fnmatch import fnmatchcase
from itertools import chain
import re
import argparse
import sys


def gen_data_lines(filename):
    with open(filename, "rt") as fin:
        for line in fin:
            if not line.startswith("#"):
                yield line


def gen_stats(data_lines):
    for line in data_lines:
        try:
            vhost, queue, _, _, m_all, _ = line.split(None, 5)
        except ValueError:
            print("ERROR: problem parsing the stats file")
            sys.exit(2)
        assert m_all.isdigit(), ("Message count is not a number: {0!r}"
                                 .format(m_all))
        yield vhost, queue, int(m_all)


def collate_stats(stats, limits):
    # Create a dict with stats collated according to the definitions in the
    # limits file. If none of the definitions in the limits file is matched,
    # store the stat without collating.
    collated = defaultdict(lambda: 0)
    for vhost, queue, m_all in stats:
        if limits:
            for limit in limits:
                vhost_matched = False
                queue_matched = False

                # if we have a vhost regex, use that to match vhosts
                # otherwise use fileglob
                if 'vhost_re' in limit:
                    if limit['vhost_re'].search(vhost): 
                        vhost_matched = True
                elif fnmatchcase(vhost, limit['vhost']):
                    vhost_matched = True

                if vhost_matched:
                    # if we have a queue regex, use that. otherwise
                    # use fileglob
                    if 'queue_re' in limit:
                        if limit['queue_re'].search(queue): 
                            queue_matched = True
                    elif fnmatchcase(queue, limit['queue']):
                        queue_matched = True
                    if queue_matched:
                        collated[limit['vhost'], limit['queue']] += m_all
                        break
        else:
            collated[vhost, queue] += m_all
    return collated


def check_stats(stats_collated, limits):
    # Create a limits lookup dict with keys of the form (vhost, queue).
    limits_lookup = dict(
        ((limit['vhost'], limit['queue']), (int(limit['warn']), int(limit['crit'])))
        for limit in limits)
    if not (stats_collated):
        yield 'No Queues Found', 'No Vhosts Found', None, "UNKNOWN"
    # Go through the stats and compare again limits, if any.
    for l_vhost, l_queue in sorted(stats_collated):
        m_all = stats_collated[l_vhost, l_queue]
        try:
            t_warning, t_critical = limits_lookup[l_vhost, l_queue]
        except KeyError:
            yield l_queue, l_vhost, m_all, "UNKNOWN"
        else:
            if m_all >= t_critical:
                yield l_queue, l_vhost, m_all, "CRIT"
            elif m_all >= t_warning:
                yield l_queue, l_vhost, m_all, "WARN"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='RabbitMQ queue size nagios check.')
    parser.add_argument(
        '-c',
        nargs=4,
        action='append',
        required=True,
        metavar=('vhost', 'queue', 'warn', 'crit'),
        help=('Vhost and queue to check. Can be used multiple times'))
    parser.add_argument(
        'stats_file',
        nargs='*',
        type=str,
        help='file containing queue stats')
    args = parser.parse_args()

    limits = []
    re_check = re.compile('^/(.*)/(i)?$')
    for l_vhost, l_queue, warn, crit in args.c:
        limit = {"vhost": l_vhost, "queue": l_queue, "warn": warn, "crit": crit}

        m = re_check.search(l_vhost)
        if m:
            if m.group(2) == 'i':
                limit['vhost_re'] = re.compile(m.group(1), re.IGNORECASE)
            else:
                limit['vhost_re'] = re.compile(m.group(1))

        m = re_check.search(l_queue)
        if m:
            if m.group(2) == 'i':
                limit['queue_re'] = re.compile(m.group(1), re.IGNORECASE)
            else:
                limit['queue_re'] = re.compile(m.group(1))
        limits.append(limit)
    print(limits)

    # Start generating stats from all files given on the command line.
    stats = gen_stats(
        chain.from_iterable(
            gen_data_lines(filename) for filename in args.stats_file))
    # Collate stats according to limit definitions and check.
    stats_collated = collate_stats(stats, limits)
    stats_checked = check_stats(stats_collated, limits)
    criticals, warnings = [], []
    for queue, vhost, message_no, status in stats_checked:
        if status == "CRIT":
            criticals.append(
                "%s in %s has %s messages" % (queue, vhost, message_no))
        elif status == "WARN":
            warnings.append(
                "%s in %s has %s messages" % (queue, vhost, message_no))
    if len(criticals) > 0:
        print("CRITICAL: {}".format(", ".join(criticals)))
        sys.exit(2)
        # XXX: No warnings if there are criticals?
    elif len(warnings) > 0:
        print("WARNING: {}".format(", ".join(warnings)))
        sys.exit(1)
    else:
        print("OK")
        sys.exit(0)
