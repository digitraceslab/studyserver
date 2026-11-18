MySQL TLS
=========

MySQL is reachable from outside and requires encrypted connections with a
publicly trusted certificate.

This page is intentionally **not** linked from any toctree; it is an internal
reference, not part of the published documentation.

.. contents::
   :local:


Overview
--------

.. list-table::
   :header-rows: 1

   * - Item
     - Value
   * - Host
     - ``aware.cs.aalto.fi``
   * - Port
     - ``<port>`` (opened by Aalto ITS)
   * - Certificate
     - Obtained with certbot, issued via Aalto ITS (~200-day validity)
   * - Cert source
     - ``/etc/letsencrypt/live/aware.cs.aalto.fi/``
   * - Cert used by mysqld
     - ``/var/lib/mysql/fullchain.pem``, ``/var/lib/mysql/privkey.pem``
   * - Renewal
     - certbot timer + deploy hook (copy + restart)


mysqld configuration
--------------------

``/etc/mysql/mysql.conf.d/mysqld.cnf``, under ``[mysqld]``:

.. code-block:: ini

   ssl_cert=/var/lib/mysql/fullchain.pem
   ssl_key=/var/lib/mysql/privkey.pem
   require_secure_transport=ON

Do not set ``ssl_ca`` — it is only for verifying client certificates.


Renewal hook
------------

mysqld runs as user ``mysql`` and cannot read ``/etc/letsencrypt`` (root-only,
and blocked by Ubuntu's AppArmor profile for mysqld). The hook copies the files
into the datadir and restarts MySQL.

``/etc/letsencrypt/renewal-hooks/deploy/mysql.sh`` (executable):

.. code-block:: sh

   #!/bin/sh
   install -o mysql -g mysql -m 600 /etc/letsencrypt/live/aware.cs.aalto.fi/privkey.pem   /var/lib/mysql/privkey.pem
   install -o mysql -g mysql -m 644 /etc/letsencrypt/live/aware.cs.aalto.fi/fullchain.pem /var/lib/mysql/fullchain.pem
   systemctl restart mysql

Restart drops open connections for a few seconds each renewal; clients must
retry. Zero-downtime alternative: replace the restart with
``mysql -e "ALTER INSTANCE RELOAD TLS"`` and give root non-interactive
credentials (``/root/.my.cnf``, mode 600).


Verification
------------

.. code-block:: sql

   SHOW VARIABLES LIKE 'ssl_%';              -- configured paths
   SHOW STATUS LIKE 'Ssl_server_not_after';  -- expiry of the cert actually served

.. code-block:: sh

   openssl s_client -starttls mysql -connect aware.cs.aalto.fi:<port> </dev/null \
     | openssl x509 -noout -subject -issuer -enddate

If ``Ssl_server_not_after`` is years in the future, mysqld is serving its
auto-generated self-signed cert instead.


Clients
-------

Use ``ssl-mode=VERIFY_IDENTITY``. With only ``REQUIRED``, traffic is encrypted
but the server is not authenticated (MITM possible).


Notes
-----

- Without ``ssl_cert``/``ssl_key``, MySQL 8 auto-generates a self-signed cert;
  connections are encrypted but unverified.
- Ports <1024 (e.g. 443) cannot be used by mysqld without extra capabilities.
- Certificate lifetimes are shrinking (CA/B Forum: 100 days from 2027, 47 days
  from 2029); renewal must stay automated.
