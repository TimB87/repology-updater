# Copyright (C) 2016-2017 Dmitry Marakasov <amdmi3@amdmi3.ru>
#
# This file is part of repology
#
# repology is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# repology is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with repology.  If not, see <http://www.gnu.org/licenses/>.

import sys

from functools import cmp_to_key

from repology.package import *
from repology.version import VersionCompare


def PackagesMerge(packages):
    aggregated = {}

    # aggregate by subrepo/name/version
    # this is just to make merging faster, as packages
    # with same subrepo/name/version may or may not merge
    for package in packages:
        key = (package.subrepo, package.name, package.version)
        aggregated.setdefault(key, []).append(package)

    outpkgs = []
    for packages in aggregated.values():
        while packages:
            nextpackages = []
            merged = packages[0]
            for package in packages[1:]:
                if not merged.TryMerge(package):
                    nextpackages.append(package)

            outpkgs.append(merged)
            packages = nextpackages

    return outpkgs


def PackagesetCheckFilters(packages, *filters):
    for filt in filters:
        if not filt.Check(packages):
            return False

    return True


def FillPackagesetVersions(packages):
    branchchecks = [
        lambda package: package.devel,
        lambda package: not package.devel
    ]

    branchclasses = [
        VersionClass.devel,
        VersionClass.newest,
    ]

    bestversions = [ None for _ in branchchecks ]

    def UpdateBestVersions(bvlist, package):
        if package.ignoreversion:
            return

        for branchnum, check in enumerate(branchchecks):
            if not check(package):
                continue

            if bvlist[branchnum] is None or VersionCompare(package.version, bvlist[branchnum]) > 0:
                bvlist[branchnum] = package.version

    # Pass 1:
    # - calculate newest version for each branch
    # - aggregate by repo
    families = set()
    packages_by_repo = {}

    for package in packages:
        UpdateBestVersions(bestversions, package)

        families.add(package.family)
        packages_by_repo.setdefault(package.repo, []).append(package)

    # for unique metapackages, replace fresh classes with unique
    if len(families) == 1:
        branchclasses = [ VersionClass.unique for _ in range(0, len(branchclasses)) ]

    # Pass 2:
    # - per-repo aggregation
    # - fill classes
    for repo, repo_packages in packages_by_repo.items():
        # Pass 2.1:
        # - determine best version for this repo
        bestversions_for_repo = [ None for _ in branchchecks ]
        for package in repo_packages:
            UpdateBestVersions(bestversions_for_repo, package)

        # Pass 2.2:
        # - fill version classes
        prevrepocmpresult = None
        for package in repo_packages:
            # ensure versionclass is reset first
            package.versionclass = None

            for bestversion, bestversion_for_repo, versionclass in zip(bestversions, bestversions_for_repo, branchclasses):
                cmpresult = VersionCompare(package.version, bestversion) if bestversion is not None else 1
                repocmpresult = VersionCompare(package.version, bestversion_for_repo) if bestversion_for_repo is not None else 1

                if cmpresult > 0:
                    # newer than newest in current branch

                    if prevrepocmpresult is None:
                        # only possible on first iteration, everything > most newest is ignored
                        package.versionclass = VersionClass.ignored
                    elif prevrepocmpresult >= 0:
                        # otherwise, we're procesing outdated from the brevious iteration
                        package.versionclass = VersionClass.outdated
                    else:
                        package.versionclass = VersionClass.legacy
                    break
                elif cmpresult == 0:
                    # newest in current branch
                    package.versionclass = versionclass
                    break

                # process what's left (outdated for this branch) on the following iteration

                prevrepocmpresult = repocmpresult

            # leftovers are outdated packages for the last branch
            if package.versionclass is None:
                if prevrepocmpresult >= 0:
                    package.versionclass = VersionClass.outdated
                else:
                    package.versionclass = VersionClass.legacy


def PackagesetToSummaries(packages):
    summary = {}

    state_by_repo = {}
    families = set()

    for package in packages:
        families.add(package.family)

        if package.repo not in state_by_repo:
            state_by_repo[package.repo] = {
                'has_outdated': False,
                'bestpackage': None,
                'count': 0
            }

        if package.versionclass == VersionClass.outdated:
            state_by_repo[package.repo]['has_outdated'] = True,

        if state_by_repo[package.repo]['bestpackage'] is None or VersionCompare(package.version, state_by_repo[package.repo]['bestpackage'].version) > 0:
            state_by_repo[package.repo]['bestpackage'] = package

        state_by_repo[package.repo]['count'] += 1

    for repo, state in state_by_repo.items():
        resulting_class = None

        # XXX: unique ignored package is currently unique; should it be ignored instead?
        if state['bestpackage'].versionclass == VersionClass.outdated:
            resulting_class = VersionClass.outdated
        elif len(families) == 1:
            resulting_class = VersionClass.unique
        elif state['bestpackage'].versionclass == VersionClass.newest:
            if state['has_outdated']:
                resulting_class = VersionClass.mixed
            else:
                resulting_class = VersionClass.newest
        elif state['bestpackage'].versionclass == VersionClass.ignored:
            resulting_class = VersionClass.ignored

        summary[repo] = {
            'version': state['bestpackage'].version,
            'bestpackage': state['bestpackage'],
            'versionclass': resulting_class,
            'numpackages': state['count']
        }

    return summary


def PackagesetSortByVersions(packages):
    def packages_version_cmp_reverse(p1, p2):
        return VersionCompare(p2.version, p1.version)

    return sorted(packages, key=cmp_to_key(packages_version_cmp_reverse))


def PackagesetToFamilies(packages):
    return set([package.family for package in packages])


def PackagesetAggregateByVersions(packages, classmap={}):
    def MapClass(versionclass):
        return classmap.get(versionclass, versionclass)

    versions = {}
    for package in packages:
        key = (package.version, MapClass(package.versionclass))
        if key not in versions:
            versions[key] = []
        versions[key].append(package)

    def key_cmp_reverse(v1, v2):
        return VersionCompare(v2[0], v1[0])

    return [
        {
            'version': key[0],
            'versionclass': key[1],
            'packages': versions[key]
        } for key in sorted(versions.keys(), key=cmp_to_key(key_cmp_reverse))
    ]
