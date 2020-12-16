# Overview

RabbitMQ is an implementation of AMQP, the emerging standard for high
performance enterprise messaging. The RabbitMQ server is a robust and scalable
implementation of an AMQP broker.

The rabbitmq-server charm deploys RabbitMQ server and provides AMQP services to
those charms that support the rabbitmq interface. The current list of such
charms can be obtained from the [Charm Store][charms-requires-rabbitmq] (the
charms officially supported by the OpenStack Charms project are published by
'openstack-charmers').

# Usage

## Configuration

This section covers common configuration options. See file `config.yaml` for
the full list of options, along with their descriptions and default values.

#### `min-cluster-size`

The `min-cluster-size` option sets the number of rabbitmq-server units required
to form its cluster. It is best practice to use this option as doing so ensures
that the charm will wait until the cluster is up before accepting relations
from other client applications.

#### `source`

The `source` option sets an alternate software source and can be passed during
or after deployment. The default behaviour is to use the Ubuntu package archive
for the underlying machine series. The most common value is a [UCA][uca] cloud
pocket (e.g. 'cloud:bionic-train'). In the case of a non-OpenStack project,
there is no guarantee that a candidate will be found in the stated UCA pocket.

> **Note:** Changing the value of this option post-deployment will trigger a
  software upgrade. See [OpenStack upgrade][cdg-upgrade-openstack] in the
  [OpenStack Charms Deployment Guide][cdg].

#### `ssl`

The `ssl` option enables encryption for client-server communication. It can
take on several values:

* 'off': disables SSL (the default)
* 'on': enables SSL for compatible clients
* 'only': enforces SSL

## Deployment

To deploy a single rabbitmq-server unit:

    juju deploy rabbitmq-server

To make use of AMQP services, simply add a relation between rabbitmq-server and
an application that supports the rabbitmq interface. For instance:

    juju add-relation rabbitmq-server:amqp nova-cloud-controller:amqp

## High availability

When more than one unit is deployed the charm will bring up a native RabbitMQ
HA active/active cluster. The ``min-cluster-size`` option should be used (see
description above).

To deploy a three-node cluster:

    juju deploy -n 3 --config min-cluster-size=3 rabbitmq-server

### SSL

Communication between the AMQP message queue and client services (OpenStack
applications) can be encrypted with SSL. There are two methods for managing
keys and certificates:

1. with Vault
1. manually (via `openssl` commands and charm options)

Vault can set up private keys and server certificates for an application. It
also stores a central CA certificate for the cloud. See the
[vault][vault-charm] charm for more information.

Vault is the recommended method and is what will be covered here.

Enable SSL by passing the `ssl` option (see description above) to the deployed
rabbitmq-server application:

    juju config rabbitmq-server ssl=only

The private key and server certificate (and its signing) are managed via a
relation made to the vault application:

    juju add-relation rabbitmq-server:certificates vault:certificates

## Actions

This section lists Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis.

* `check-queues`
* `cluster-status`
* `complete-cluster-series-upgrade`
* `list-unconsumed-queues`
* `pause`
* `resume`

To display action descriptions run `juju actions rabbitmq-server`. If the charm
is not deployed then see file ``actions.yaml``.

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-rabbitmq-server].

For general charm questions refer to the [OpenStack Charm Guide][cg].

<!-- LINKS -->

[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[cdg-upgrade-openstack]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/upgrade-openstack.html
[lp-bugs-charm-rabbitmq-server]: https://bugs.launchpad.net/charm-rabbitmq-server/+filebug
[juju-docs-actions]: https://jaas.ai/docs/actions
[charms-requires-rabbitmq]: https://jaas.ai/search?requires=rabbitmq
[vault-charm]: https://jaas.ai/vault
[uca]: https://wiki.ubuntu.com/OpenStack/CloudArchive
