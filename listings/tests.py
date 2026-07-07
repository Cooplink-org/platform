from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient

from .models import Category, Project

User = get_user_model()


class PublicProjectListTest(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Get or create category (seeded by migration)
        self.category, _ = Category.objects.get_or_create(
            name="Web apps", defaults={"slug": "web-apps"}
        )

        # Create seller user
        self.seller = User.objects.create_user(
            username="seller", email="seller@example.com", is_seller=True
        )

        # Create published projects
        self.published1 = Project.objects.create(
            title="Project A",
            slug="project-a",
            description="Description for project A",
            price="10000",
            status=Project.Status.PUBLISHED,
            seller=self.seller,
            category=self.category,
            tags=["python", "django"],
            tech_stack=["Python", "Django"],
            cover_image="https://example.com/cover1.png",
            screenshots=["https://example.com/screenshot1.png"],
            demo_url="https://demo.example.com",
        )

        self.published2 = Project.objects.create(
            title="Project B",
            slug="project-b",
            description="Description for project B",
            price="20000",
            status=Project.Status.PUBLISHED,
            seller=self.seller,
            category=self.category,
            tags=["python"],
            tech_stack=["Python"],
            view_count=50,
        )

        now = timezone.now()
        Project.objects.filter(pk=self.published1.pk).update(created_at=now - timedelta(hours=1))
        Project.objects.filter(pk=self.published2.pk).update(created_at=now)
        self.published1.refresh_from_db()
        self.published2.refresh_from_db()

        # Create draft project (should not appear in public list)
        self.draft = Project.objects.create(
            title="Draft Project",
            slug="draft-project",
            description="This is a draft",
            price="5000",
            status=Project.Status.DRAFT,
            seller=self.seller,
        )

    def test_list_returns_only_published(self):
        resp = self.client.get(reverse("public_project_list"))
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 2)
        slugs = [p["slug"] for p in results]
        self.assertIn("project-a", slugs)
        self.assertIn("project-b", slugs)
        self.assertNotIn("draft-project", slugs)

    def test_list_includes_required_fields(self):
        resp = self.client.get(reverse("public_project_list"))
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"][0]
        self.assertIn("title", result)
        self.assertIn("slug", result)
        self.assertIn("description", result)
        self.assertIn("price", result)
        self.assertIn("tags", result)
        self.assertIn("tech_stack", result)
        self.assertIn("cover_image", result)
        self.assertIn("screenshots", result)
        self.assertIn("demo_url", result)
        self.assertIn("category_name", result)
        self.assertIn("seller_profile", result)
        self.assertIn("view_count", result)

    def test_list_hides_private_repo_details(self):
        resp = self.client.get(reverse("public_project_list"))
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"][0]
        # Should NOT include github_repo_full_name
        self.assertNotIn("github_repo_full_name", result)
        self.assertNotIn("github_default_branch", result)
        self.assertNotIn("license_type", result)
        self.assertNotIn("status", result)

    def test_seller_profile_included(self):
        resp = self.client.get(reverse("public_project_list"))
        self.assertEqual(resp.status_code, 200)
        result = resp.json()["results"][0]
        seller = result["seller_profile"]
        self.assertEqual(seller["username"], "seller")
        self.assertEqual(seller["avatar_url"], "")
        self.assertIsNone(seller["bio"])

    def test_filter_by_category(self):
        # Use seeded category "Web apps"
        resp = self.client.get(f"{reverse('public_project_list')}?category={self.category.slug}")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 2)

    def test_filter_by_tags(self):
        resp = self.client.get(f"{reverse('public_project_list')}?tags=python")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 2)

    def test_filter_by_tags_multiple(self):
        resp = self.client.get(f"{reverse('public_project_list')}?tags=python,django")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-a")

    def test_filter_by_price_range(self):
        resp = self.client.get(f"{reverse('public_project_list')}?min_price=15000")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-b")

    def test_filter_by_price_range_max(self):
        resp = self.client.get(f"{reverse('public_project_list')}?max_price=15000")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-a")

    def test_filter_by_tech_stack(self):
        resp = self.client.get(f"{reverse('public_project_list')}?tech_stack=Django")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-a")

    def test_search_by_title(self):
        resp = self.client.get(f"{reverse('public_project_list')}?q=Project%20A")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-a")

    def test_search_by_description(self):
        resp = self.client.get(f"{reverse('public_project_list')}?q=project%20B")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-b")

    def test_ordering_by_newest(self):
        resp = self.client.get(reverse("public_project_list"))
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        # Default ordering is -created_at (newest first)
        # published2 was created after published1 in setUp, so it should be first
        self.assertEqual(results[0]["slug"], "project-b")

    def test_ordering_by_price_ascending(self):
        resp = self.client.get(f"{reverse('public_project_list')}?ordering=price")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(results[0]["slug"], "project-a")  # 10000 < 20000
        self.assertEqual(results[1]["slug"], "project-b")

    def test_ordering_by_price_descending(self):
        resp = self.client.get(f"{reverse('public_project_list')}?ordering=-price")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(results[0]["slug"], "project-b")  # 20000 > 10000
        self.assertEqual(results[1]["slug"], "project-a")

    def test_ordering_by_popularity(self):
        resp = self.client.get(f"{reverse('public_project_list')}?ordering=-view_count")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(results[0]["slug"], "project-b")  # view_count=50
        self.assertEqual(results[1]["slug"], "project-a")  # view_count=0

    def test_pagination(self):
        resp = self.client.get(reverse("public_project_list") + "?page_size=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 1)
        self.assertIn("next", data)
        self.assertIn("previous", data)


class ProjectDetailPublicTest(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Create seller
        self.seller = User.objects.create_user(
            username="seller", email="seller@example.com", is_seller=True
        )

        # Create published project
        self.published = Project.objects.create(
            title="Test Project",
            slug="test-project",
            description="Test description",
            price="10000",
            status=Project.Status.PUBLISHED,
            seller=self.seller,
            tech_stack=["Python", "Django"],
        )

        # Create draft project
        self.draft = Project.objects.create(
            title="Draft Project",
            slug="draft-project",
            description="Draft",
            price="5000",
            status=Project.Status.DRAFT,
            seller=self.seller,
        )

    def test_detail_returns_project(self):
        resp = self.client.get(reverse("project_detail_public", args=["test-project"]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["title"], "Test Project")
        self.assertEqual(data["slug"], "test-project")
        self.assertEqual(data["description"], "Test description")
        self.assertEqual(data["price"], "10000.00")

    def test_detail_includes_required_fields(self):
        resp = self.client.get(reverse("project_detail_public", args=["test-project"]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("title", data)
        self.assertIn("slug", data)
        self.assertIn("description", data)
        self.assertIn("price", data)
        self.assertIn("tags", data)
        self.assertIn("tech_stack", data)
        self.assertIn("cover_image", data)
        self.assertIn("screenshots", data)
        self.assertIn("demo_url", data)
        self.assertIn("category_name", data)
        self.assertIn("seller_profile", data)
        self.assertIn("view_count", data)

    def test_detail_hides_private_repo_details(self):
        resp = self.client.get(reverse("project_detail_public", args=["test-project"]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotIn("github_repo_full_name", data)
        self.assertNotIn("github_default_branch", data)
        self.assertNotIn("license_type", data)
        self.assertNotIn("status", data)

    def test_detail_increments_view_count(self):
        self.assertEqual(self.published.view_count, 0)
        self.client.get(reverse("project_detail_public", args=["test-project"]))
        self.published.refresh_from_db()
        self.assertEqual(self.published.view_count, 1)

        self.client.get(reverse("project_detail_public", args=["test-project"]))
        self.published.refresh_from_db()
        self.assertEqual(self.published.view_count, 2)

    def test_detail_404_for_draft(self):
        resp = self.client.get(reverse("project_detail_public", args=["draft-project"]))
        self.assertEqual(resp.status_code, 404)

    def test_detail_404_for_nonexistent(self):
        resp = self.client.get(reverse("project_detail_public", args=["nonexistent"]))
        self.assertEqual(resp.status_code, 404)

    def test_seller_profile_format(self):
        resp = self.client.get(reverse("project_detail_public", args=["test-project"]))
        self.assertEqual(resp.status_code, 200)
        seller = resp.json()["seller_profile"]
        self.assertEqual(seller["username"], "seller")


class CategoryModelTest(TestCase):
    def test_category_str(self):
        cat = Category.objects.create(name="Test Category", slug="test-category")
        self.assertEqual(str(cat), "Test Category")

    def test_category_slug_auto_generated(self):
        cat = Category.objects.create(name="Test Category")
        self.assertEqual(cat.slug, "test-category")

    def test_category_unique_name(self):
        Category.objects.create(name="Unique Category")
        with self.assertRaises(Exception):
            Category.objects.create(name="Unique Category")