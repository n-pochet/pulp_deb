# Generated by Django 3.2.19 on 2023-05-09 12:35, extended manually;

import logging

from datetime import datetime

from django.db import migrations, models
from django.core.exceptions import ObjectDoesNotExist

BATCH_SIZE = 1000

log = logging.getLogger(__name__)


def merge_colliding_structure_content(apps, schema_editor):
    ReleaseArchitecture = apps.get_model('deb', 'ReleaseArchitecture')
    ReleaseComponent = apps.get_model('deb', 'ReleaseComponent')
    PackageReleaseComponent = apps.get_model('deb', 'PackageReleaseComponent')
    RepositoryContent = apps.get_model('core', 'RepositoryContent')
    RepositoryVersion = apps.get_model('core', 'RepositoryVersion')

    print("\n")
    log.info("{}: Starting data migration!".format(datetime.now()))

    def _get_content_repo_version_set(repo_version_set, repo_content):
        version_added = repo_content.version_added.number
        if repo_content.version_removed:
            version_removed = repo_content.version_added.number
        else:
            version_removed = max(repo_version_set) + 1
        return set([n for n in repo_version_set if version_added <= n < version_removed])

    def _get_repo_content_to_update(duplicate_content_ids, content_to_keep):
        # Note that len(duplicate_content_ids) is expected to be much smaller than BATCH_SIZE.
        # We don't care if the batch is up to len(duplicate_content_ids) larger than BATCH_SIZE.
        repo_content_to_update = []
        for duplicate_content in RepositoryContent.objects.filter(
            content_id__in=duplicate_content_ids
        ):
            repo_version_set = set(
                RepositoryVersion.objects.filter(
                    repository_id=duplicate_content.repository_id
                ).values_list('number', flat=True)
            )
            for keep_content in RepositoryContent.objects.filter(
                content_id=content_to_keep, repository_id=duplicate_content.repository_id
            ):
                if not keep_content.version_removed and not duplicate_content.version_removed:
                    # Neither repo_content was ever removed.
                    first_added = min(
                        keep_content.version_added.number,
                        duplicate_content.version_added.number,
                    )
                    if keep_content.version_added.number != first_added:
                        keep_content.version_added = duplicate_content.version_added
                        keep_content.save()
                    message = '{}: Merging repo_content "{}" into "{}".'
                    log.info(
                        message.format(
                            datetime.now(), duplicate_content.pulp_id, keep_content.pulp_id
                        )
                    )
                    duplicate_content.delete()  # Does this work?
                    duplicate_content = keep_content
                elif keep_content.version_removed and duplicate_content.version_removed:
                    # Both repo_contents were rmoved at some point.
                    versions1 = _get_content_repo_version_set(repo_version_set, keep_content)
                    versions2 = _get_content_repo_version_set(repo_version_set, duplicate_content)
                    if versions1.intersection(versions2):
                        # The two repo_content overlap.
                        joint_version_range = versions1.union(versions2)
                        first_added = min(joint_version_range)
                        last_removed = max(joint_version_range)
                        if keep_content.version_added.number != first_added:
                            keep_content.version_added = duplicate_content.version_added
                        if keep_content.version_removed.number != last_removed:
                            keep_content.version_removed = duplicate_content.version_removed
                        message = '{}: Merging repo_content "{}" into "{}".'
                        log.info(
                            message.format(
                                datetime.now(), duplicate_content.pulp_id, keep_content.pulp_id
                            )
                        )
                        keep_content.save()
                        duplicate_content.delete()  # Does this work?
                        duplicate_content = keep_content
                else:
                    # Exactly one repo_content has already been removed
                    versions1 = _get_content_repo_version_set(repo_version_set, keep_content)
                    versions2 = _get_content_repo_version_set(repo_version_set, duplicate_content)
                    if versions1.intersection(versions2):
                        # The two repo_content overlap.
                        first_added = min(versions1.union(versions2))
                        if keep_content.version_added.number != first_added:
                            keep_content.version_added = duplicate_content.version_added
                        if keep_content.version_removed:
                            keep_content.version_removed = None
                        message = '{}: Merging repo_content "{}" into "{}".'
                        log.info(
                            message.format(
                                datetime.now(), duplicate_content.pulp_id, keep_content.pulp_id
                            )
                        )
                        keep_content.save()
                        duplicate_content.delete()  # Does this work?
                        duplicate_content = keep_content

            duplicate_content.content_id = content_to_keep
            repo_content_to_update.append(duplicate_content)

        return repo_content_to_update

    def _deduplicate_PRC(duplicate_component, component_to_keep):
        duplicate_prcs = PackageReleaseComponent.objects.filter(
            release_component=duplicate_component
        )
        repo_content_to_update = []
        for duplicate_prc in duplicate_prcs.iterator(chunk_size=BATCH_SIZE):
            try:
                prc_to_keep = PackageReleaseComponent.objects.get(
                    release_component=component_to_keep,
                    package=duplicate_prc.package,
                )
            except ObjectDoesNotExist:
                component = ReleaseComponent.objects.get(pk=component_to_keep)
                prc_to_keep = PackageReleaseComponent.objects.create(
                    pulp_type='deb.package_release_component',
                    release_component=component,
                    package=duplicate_prc.package,
                )

            repo_content_to_update += _get_repo_content_to_update(
                [duplicate_prc.pk], prc_to_keep.pk
            )

            if len(repo_content_to_update) >= BATCH_SIZE:
                RepositoryContent.objects.bulk_update(repo_content_to_update, ["content_id"])

        # Handle remaining content <= BATCH_SIZE:
        if len(repo_content_to_update) > 0:
            RepositoryContent.objects.bulk_update(repo_content_to_update, ["content_id"])

        PackageReleaseComponent.objects.filter(pk__in=duplicate_prcs).delete()

    # Deduplicate ReleaseArchitecture:
    distributions = (
        ReleaseArchitecture.objects.all()
        .distinct('distribution')
        .values_list('distribution', flat=True)
    )

    for distribution in distributions:
        architectures = (
            ReleaseArchitecture.objects.filter(distribution=distribution)
            .distinct('architecture')
            .values_list('architecture', flat=True)
        )
        architecture_ids_to_delete = []
        repo_content_to_update = []
        for architecture in architectures:
            duplicate_architecture_ids = list(
                ReleaseArchitecture.objects.filter(
                    distribution=distribution, architecture=architecture
                ).values_list('pk', flat=True)
            )
            if len(duplicate_architecture_ids) > 1:
                architecture_to_keep = duplicate_architecture_ids.pop()
                message = (
                    '{}: Merging duplicates for architecture "{}" in distribution "{}" into '
                    'ReleaseArchitecture "{}"!'
                )
                log.info(
                    message.format(datetime.now(), architecture, distribution, architecture_to_keep)
                )
                architecture_ids_to_delete += duplicate_architecture_ids
                repo_content_to_update += _get_repo_content_to_update(
                    duplicate_architecture_ids, architecture_to_keep
                )

            if len(architecture_ids_to_delete) >= BATCH_SIZE:
                # We assume len(repo_content_to_update)==len(architecture_ids_to_delete)!
                RepositoryContent.objects.bulk_update(repo_content_to_update, ["content_id"])
                repo_content_to_update = []

                ReleaseArchitecture.objects.filter(pk__in=architecture_ids_to_delete).delete()
                architecture_ids_to_delete = []

        # Handle remaining content <= BATCH_SIZE:
        if len(repo_content_to_update) > 0:
            RepositoryContent.objects.bulk_update(repo_content_to_update, ["content_id"])

        if len(architecture_ids_to_delete) > 0:
            ReleaseArchitecture.objects.filter(pk__in=architecture_ids_to_delete).delete()

    # Deduplicate ReleaseComponent:
    distributions = (
        ReleaseComponent.objects.all()
        .distinct('distribution')
        .values_list('distribution', flat=True)
    )
    for distribution in distributions:
        components = (
            ReleaseComponent.objects.filter(distribution=distribution)
            .distinct('component')
            .values_list('component', flat=True)
        )
        component_ids_to_delete = []
        repo_content_to_update = []
        for component in components:
            duplicate_component_ids = list(
                ReleaseComponent.objects.filter(
                    distribution=distribution, component=component
                ).values_list('pk', flat=True)
            )
            if len(duplicate_component_ids) > 1:
                component_to_keep = duplicate_component_ids.pop()
                message = (
                    '{}: Merging duplicates for component "{}" in distribution "{}" into '
                    'ReleaseComponent "{}"!'
                )
                log.info(message.format(datetime.now(), component, distribution, component_to_keep))
                component_ids_to_delete += duplicate_component_ids
                repo_content_to_update += _get_repo_content_to_update(
                    duplicate_component_ids, component_to_keep
                )

                # Deduplicate PackageReleaseComponents
                for duplicate_component in duplicate_component_ids:
                    message = (
                        '{}: Handling PackageReleaseComponents for duplicate ReleaseComponent "{}"!'
                    )
                    log.info(message.format(datetime.now(), duplicate_component))
                    _deduplicate_PRC(duplicate_component, component_to_keep)

            if len(component_ids_to_delete) >= BATCH_SIZE:
                # We assume len(repo_content_to_update)==len(component_ids_to_delete)!
                RepositoryContent.objects.bulk_update(repo_content_to_update, ["content_id"])
                repo_content_to_update = []

                ReleaseComponent.objects.filter(pk__in=component_ids_to_delete).delete()
                component_ids_to_delete = []

        # Handle remaining content <= BATCH_SIZE:
        if len(repo_content_to_update) > 0:
            RepositoryContent.objects.bulk_update(repo_content_to_update, ["content_id"])

        if len(component_ids_to_delete) > 0:
            ReleaseComponent.objects.filter(pk__in=component_ids_to_delete).delete()

    log.info("{}: Data migration completed!\n".format(datetime.now()))


class Migration(migrations.Migration):
    dependencies = [
        ('deb', '0024_add_release_fields'),
    ]

    operations = [
        migrations.RunPython(
            merge_colliding_structure_content, reverse_code=migrations.RunPython.noop, elidable=True
        ),
        migrations.RunSQL(
            sql="SET CONSTRAINTS ALL IMMEDIATE;",
            reverse_sql="",
        ),
        migrations.AlterUniqueTogether(
            name='releasearchitecture',
            unique_together={('distribution', 'architecture')},
        ),
        migrations.AlterUniqueTogether(
            name='releasecomponent',
            unique_together={('distribution', 'component')},
        ),
        # Give a default value to fields for the sake of back migrating
        migrations.AlterField(
            model_name='releasearchitecture',
            name='codename',
            field=models.TextField(default=''),
        ),
        migrations.AlterField(
            model_name='releasearchitecture',
            name='suite',
            field=models.TextField(default=''),
        ),
        migrations.AlterField(
            model_name='releasecomponent',
            name='codename',
            field=models.TextField(default=''),
        ),
        migrations.AlterField(
            model_name='releasecomponent',
            name='suite',
            field=models.TextField(default=''),
        ),
        # Before dropping the fields for good!
        migrations.RemoveField(
            model_name='releasearchitecture',
            name='codename',
        ),
        migrations.RemoveField(
            model_name='releasearchitecture',
            name='suite',
        ),
        migrations.RemoveField(
            model_name='releasecomponent',
            name='codename',
        ),
        migrations.RemoveField(
            model_name='releasecomponent',
            name='suite',
        ),
    ]
