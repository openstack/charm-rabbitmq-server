# Overview

RabbitMQ is an implementation of AMQP, the emerging standard for high performance enterprise messaging.

The RabbitMQ server is a robust and scalable implementation of an AMQP broker.

This charm deploys RabbitMQ server and provides AMQP connectivity to clients.

# Usage

To deploy this charm:

    juju deploy rabbitmq-server

deploying multiple units will form a native RabbitMQ cluster:

    juju deploy -n 3 rabbitmq-server
    juju config rabbitmq-server min-cluster-size=3

To make use of AMQP services, simply relate other charms that support the rabbitmq interface:

    juju add-relation rabbitmq-server nova-cloud-controller

# Clustering

When more than one unit of the charm is deployed the charm will bring up a
native RabbitMQ cluster. The process of clustering the units together takes
some time. Due to the nature of asynchronous hook execution, it is possible
client relationship hooks are executed before the cluster is complete.
In some cases, this can lead to client charm errors.

To guarantee client relation hooks will not be executed until clustering is
completed use the min-cluster-size configuration setting:

    juju deploy -n 3 rabbitmq-server
    juju config rabbitmq-server min-cluster-size=3

When min-cluster-size is not set the charm will still cluster, however,
there are no guarantees client relation hooks will not execute before it is
complete.

Single unit deployments behave as expected.

# Configuration: SSL

Generate an unencrypted RSA private key for the servers and a certificate:

    openssl genrsa -out rabbit-server-privkey.pem 2048

Get an X.509 certificate. This can be self-signed, for example:

    openssl req -batch -new -x509 -key rabbit-server-privkey.pem -out rabbit-server-cert.pem -days 10000

Deploy the service:

    juju deploy rabbitmq-server

Enable SSL, passing in the key and certificate as configuration settings:

    juju set rabbitmq-server ssl_enabled=True ssl_key="`cat rabbit-server-privkey.pem`" ssl_cert="`cat rabbit-server-cert.pem`"

# Monitoring

This charm supports monitoring queue thresholds to alert when queues are
becoming full or are not being processed in a timely fashion. This is 
done via the `queue_thresholds` config option.

`queue_thresholds` is a list of queue check thresholds in yaml format.

Each item in the list represents a single check and is defined as 
an array containing the vhost to check, the specific queue (or queue
match string), the warning level, and the critical level. An example 
would be:

    [
        ['/', 'queue1', 50, 100]
        ['/', 'queue2', 200, 300]
    ]

This would create two checks, one on queue1 that has a warn limit of
50, and a critical limit of 100, and a second on queue2 that warns at
200, and has a critical limit of 300.

You can create a check that monitors several queues at once by using 
fileglob matching, or regex matching, though the format is slightly
different.

For simple matches, you can use * to match portions of an event queue
name.  For example:

    [
        ['/', 'queue*', 50, 100]
    ]

would create a check that would monitor any queue beginning with the
word queue and containing any additional characters after.  

To use regex, you must wrap the queue name in / characters, and provide 
a python-compatible regex.  For example:

    [
        ['/', '/^queue\d+$/', 50, 100]
    ]

Would match any queue that starts with the word `queue` and contains
any number of digits following it.  Note that these regex's are python
compatible, meaning that to negate a match, for example, you need
to use the `(?!foo)` format.

It should be noted that when using queue matching, the levels provided
are cumulative.  That is to say that a critical level of 100 would
trigger if all of the matched queues added together contained 100 
messages, even if some of those queues contained nothing at all.

# Configuration: source

To change the source that the charm uses for packages:

    juju set rabbitmq-server source="cloud:precise-icehouse"

This will enable the Icehouse pocket of the Cloud Archive (which contains a new version of RabbitMQ) and upgrade the install to the new version.

The source option can be used in a few different ways:

    source="ppa:james-page/testing" - use the testing PPA owned by james-page
    source="http://myrepo/ubuntu main" - use the repository located at the provided URL

The charm also supports use of arbitrary archive key's for use with private repositories:

    juju set rabbitmq-server key="C6CEA0C9"

Note that in clustered configurations, the upgrade can be a bit racey as the services restart and re-cluster; this is resolvable using (with Juju version < 2.0) :

    juju resolved --retry rabbitmq-server/1

Or using the following command with Juju 2.0 and above:

    juju resolved rabbitmq-server/1

# Network Spaces support

This charm supports the use of Juju Network Spaces, allowing the charm to be bound to network space configurations managed directly by Juju.  This is only supported with Juju 2.0 and above.

The amqp relation can be bound to a specific network space, allowing client connections to be routed over specific networks:

    juju deploy rabbitmq-server --bind "amqp=internal-space"

alternatively this can also be provided as part of a juju native bundle configuration:

    rabbitmq-server:
      charm: cs:xenial/rabbitmq-server
      num_units: 1
      bindings:
        amqp: internal-space

**NOTE:** Spaces must be configured in the underlying provider prior to attempting to use them.

**NOTE:** Existing deployments using the access-network configuration option will continue to function; this option is preferred over any network space binding provided if set.

# Contact Information

Author: OpenStack Charmers <openstack-charmers@lists.ubuntu.com>
Bugs: http://bugs.launchpad.net/charms/+source/rabbitmq-server/+filebug
Location: http://jujucharms.com/rabbitmq-server
