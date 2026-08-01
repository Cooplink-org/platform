from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from orders.models import Order

from .models import Category, Project, Rating

User = get_user_model()

APIView.throttle_classes = []
SimpleRateThrottle.THROTTLE_RATES = {"anon": None, "user": None, "burst": None}


def _bearer(user):
    return f"Bearer {RefreshToken.for_user(user).access_token}"


def _onboard(user):
    user.full_legal_name = "Test User"
    user.phone_number = "+998901234567"
    user.avatar_url = "https://avatars.githubusercontent.com/u/1"
    user.terms_accepted_version = settings.CURRENT_TERMS_VERSION
    user.terms_accepted_at = timezone.now()
    user.save(
        update_fields=[
            "full_legal_name",
            "phone_number",
            "avatar_url",
            "terms_accepted_version",
            "terms_accepted_at",
        ]
    )
    return user


def _seller(**kwargs):
    u = User.objects.create_user(is_seller=True, **kwargs)
    u.github_token_encrypted = "gAAAAABmockedencryptedtoken=="
    u.save(update_fields=["github_token_encrypted"])
    return _onboard(u)


def _buyer(**kwargs):
    return _onboard(User.objects.create_user(**kwargs))


def _category(name="Web apps"):
    cat, _ = Category.objects.get_or_create(name=name, defaults={"slug": "web-apps"})
    return cat


def _published_project(seller, title="Test Project", **kw):
    defaults = dict(
        title=title,
        slug=title.lower().replace(" ", "-"),
        description="Description",
        price="10000.00",
        status=Project.Status.PUBLISHED,
        seller=seller,
        category=_category(),
        tech_stack=["Python"],
    )
    defaults.update(kw)
    return Project.objects.create(**defaults)


# ── public project list ───────────────────────────────────────────────────────


class PublicProjectListTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = _category()
        self.seller = _seller(username="seller", email="seller@example.com")
        self.published1 = _published_project(
            self.seller,
            "Project A",
            tags=["python", "django"],
            view_count=0,
        )
        self.published2 = _published_project(
            self.seller,
            "Project B",
            tags=["python"],
            view_count=50,
            price="20000.00",
            tech_stack=["Python", "Django"],
        )
        now = timezone.now()
        Project.objects.filter(pk=self.published1.pk).update(created_at=now - timedelta(hours=1))
        Project.objects.filter(pk=self.published2.pk).update(created_at=now)
        self.published1.refresh_from_db()
        self.published2.refresh_from_db()

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
        slugs = [p["slug"] for p in resp.json()["results"]]
        self.assertIn("project-a", slugs)
        self.assertNotIn("draft-project", slugs)

    def test_list_includes_required_fields(self):
        resp = self.client.get(reverse("public_project_list"))
        result = resp.json()["results"][0]
        # Lightweight list serializer excludes description/screenshots/demo_url
        expected = {
            "title",
            "slug",
            "price",
            "tags",
            "tech_stack",
            "cover_image",
            "category_name",
            "seller_username",
            "seller_avatar",
            "view_count",
            "featured",
            "average_rating",
            "rating_count",
            "created_at",
            "id",
        }
        self.assertTrue(expected.issubset(set(result.keys())))

    def test_list_hides_private_repo_details(self):
        result = self.client.get(reverse("public_project_list")).json()["results"][0]
        self.assertNotIn("github_repo_full_name", result)
        self.assertNotIn("github_default_branch", result)
        self.assertNotIn("license_type", result)
        self.assertNotIn("status", result)

    def test_list_excludes_large_fields_for_efficiency(self):
        """The list endpoint should not return description/screenshots (detail-only fields)."""
        result = self.client.get(reverse("public_project_list")).json()["results"][0]
        self.assertNotIn("description", result)
        self.assertNotIn("screenshots", result)
        self.assertNotIn("demo_url", result)

    def test_seller_info_included_in_list(self):
        result = self.client.get(reverse("public_project_list")).json()["results"][0]
        self.assertIn("seller_username", result)
        self.assertIn("seller_avatar", result)

    def test_filter_by_category(self):
        resp = self.client.get(f"{reverse('public_project_list')}?category={self.category.slug}")
        self.assertEqual(len(resp.json()["results"]), 2)

    def test_filter_by_tags(self):
        resp = self.client.get(f"{reverse('public_project_list')}?tags=python")
        self.assertEqual(len(resp.json()["results"]), 2)

    def test_filter_by_tags_multiple(self):
        resp = self.client.get(f"{reverse('public_project_list')}?tags=python,django")
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-a")

    def test_filter_by_price_range(self):
        resp = self.client.get(f"{reverse('public_project_list')}?min_price=15000")
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-b")

    def test_filter_by_price_range_max(self):
        resp = self.client.get(f"{reverse('public_project_list')}?max_price=15000")
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-a")
        self.assertEqual(results[0]["price"], "10000.00")

    def test_filter_by_tech_stack(self):
        resp = self.client.get(f"{reverse('public_project_list')}?tech_stack=Django")
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-b")

    def test_search_by_title(self):
        resp = self.client.get(f"{reverse('public_project_list')}?q=Project%20A")
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)

    def test_search_by_description(self):
        resp = self.client.get(f"{reverse('public_project_list')}?q=project%20B")
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)

    def test_ordering_default_newest_first(self):
        results = self.client.get(reverse("public_project_list")).json()["results"]
        self.assertEqual(results[0]["slug"], "project-b")
        self.assertEqual(results[1]["slug"], "project-a")

    def test_ordering_by_price_ascending(self):
        results = self.client.get(f"{reverse('public_project_list')}?ordering=price").json()[
            "results"
        ]
        self.assertEqual(results[0]["slug"], "project-a")

    def test_ordering_by_price_descending(self):
        results = self.client.get(f"{reverse('public_project_list')}?ordering=-price").json()[
            "results"
        ]
        self.assertEqual(results[0]["slug"], "project-b")

    def test_ordering_by_popularity(self):
        results = self.client.get(f"{reverse('public_project_list')}?ordering=-view_count").json()[
            "results"
        ]
        self.assertEqual(results[0]["slug"], "project-b")

    def test_pagination(self):
        resp = self.client.get(reverse("public_project_list") + "?page_size=1")
        data = resp.json()
        self.assertEqual(len(data["results"]), 1)
        self.assertIn("next", data)
        self.assertIn("previous", data)

    def test_featured_filter(self):
        self.published1.featured = True
        self.published1.save(update_fields=["featured"])
        resp = self.client.get(f"{reverse('public_project_list')}?featured=1")
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "project-a")

    def test_create_listing_page_header(self):
        resp = self.client.get(reverse("public_project_list"))
        self.assertEqual(resp.status_code, 200)

    def test_category_list(self):
        resp = self.client.get(reverse("category_list"))
        self.assertEqual(resp.status_code, 200)
        names = [c["name"] for c in resp.json()]
        self.assertIn("Web apps", names)


# ── project detail public ─────────────────────────────────────────────────────


class ProjectDetailPublicTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = _seller(username="seller", email="seller@test.com")
        self.published = _published_project(self.seller)
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
        self.assertEqual(data["price"], "10000.00")

    def test_detail_includes_public_fields(self):
        data = self.client.get(reverse("project_detail_public", args=["test-project"])).json()
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
        self.assertNotIn("github_repo_full_name", data)
        self.assertNotIn("status", data)

    def test_detail_increments_view_count(self):
        self.assertEqual(self.published.view_count, 0)
        self.client.get(reverse("project_detail_public", args=["test-project"]))
        self.published.refresh_from_db()
        self.assertEqual(self.published.view_count, 1)

    def test_detail_404_for_draft(self):
        resp = self.client.get(reverse("project_detail_public", args=["draft-project"]))
        self.assertEqual(resp.status_code, 404)

    def test_detail_404_for_nonexistent(self):
        resp = self.client.get(reverse("project_detail_public", args=["nonexistent"]))
        self.assertEqual(resp.status_code, 404)

    def test_detail_seller_profile(self):
        seller = self.client.get(reverse("project_detail_public", args=["test-project"])).json()[
            "seller_profile"
        ]
        self.assertEqual(seller["username"], "seller")


# ── seller CRUD ───────────────────────────────────────────────────────────────


class SellerProjectCRUDTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = _seller(username="seller", email="sell@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller))
        self.cat = _category()
        self.list_url = reverse("project_list_create")

    def test_create_project(self):
        resp = self.client.post(
            self.list_url,
            {
                "title": "New Project",
                "github_repo_full_name": "user/repo",
                "description": "A new project",
                "price": "50000.00",
                "category": self.cat.id,
                "tech_stack": ["Python"],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["title"], "New Project")
        self.assertEqual(data["status"], "draft")
        self.assertEqual(data["seller"], self.seller.pk)

    def test_create_project_requires_seller_auth(self):
        regular = _buyer(username="regular", email="reg@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(regular))
        resp = self.client.post(
            self.list_url,
            {
                "title": "Test",
                "github_repo_full_name": "u/r",
                "description": "d",
                "price": "1000",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_create_project_requires_auth(self):
        resp = APIClient().post(
            self.list_url,
            {
                "title": "Test",
                "github_repo_full_name": "u/r",
                "description": "d",
                "price": "1000",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_list_own_projects(self):
        Project.objects.create(
            title="My Project",
            slug="my-project",
            description="d",
            price="1000",
            status=Project.Status.DRAFT,
            seller=self.seller,
        )
        resp = self.client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        titles = [p["title"] for p in resp.json()]
        self.assertIn("My Project", titles)

    def test_detail_own_project(self):
        proj = Project.objects.create(
            title="Detail Project",
            slug="detail-project",
            description="d",
            price="1000",
            status=Project.Status.DRAFT,
            seller=self.seller,
        )
        resp = self.client.get(reverse("project_detail", args=[proj.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], "Detail Project")

    def test_detail_not_own_project_returns_404(self):
        other = _seller(username="other", email="other@test.com")
        proj = Project.objects.create(
            title="Other's",
            slug="others",
            description="d",
            price="1000",
            status=Project.Status.DRAFT,
            seller=other,
        )
        resp = self.client.get(reverse("project_detail", args=[proj.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_patch_own_draft(self):
        proj = Project.objects.create(
            title="Patchable",
            slug="patchable",
            description="d",
            price="1000",
            status=Project.Status.DRAFT,
            seller=self.seller,
        )
        resp = self.client.patch(
            reverse("project_detail", args=[proj.pk]), {"title": "Updated"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        proj.refresh_from_db()
        self.assertEqual(proj.title, "Updated")

    def test_patch_allowed_on_published(self):
        proj = _published_project(self.seller, "Published Proj")
        resp = self.client.patch(
            reverse("project_detail", args=[proj.pk]), {"title": "Updated Published"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        proj.refresh_from_db()
        self.assertEqual(proj.title, "Updated Published")

    def test_delete_own_draft(self):
        proj = Project.objects.create(
            title="Deletable",
            slug="deletable",
            description="d",
            price="1000",
            status=Project.Status.DRAFT,
            seller=self.seller,
        )
        resp = self.client.delete(reverse("project_detail", args=[proj.pk]))
        self.assertEqual(resp.status_code, 204)

    def test_submit_project(self):
        proj = Project.objects.create(
            title="Submittable",
            slug="submittable",
            description="d",
            price="1000",
            status=Project.Status.DRAFT,
            seller=self.seller,
        )
        resp = self.client.post(
            reverse("project_submit", args=[proj.pk]), {"accept_terms": True}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        proj.refresh_from_db()
        self.assertEqual(proj.status, Project.Status.PENDING_REVIEW)

    def test_submit_without_terms_rejected(self):
        proj = Project.objects.create(
            title="No Terms",
            slug="no-terms",
            description="d",
            price="1000",
            status=Project.Status.DRAFT,
            seller=self.seller,
        )
        resp = self.client.post(reverse("project_submit", args=[proj.pk]), {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_submit_non_draft_rejected(self):
        proj = _published_project(self.seller, "Already Published")
        resp = self.client.post(
            reverse("project_submit", args=[proj.pk]), {"accept_terms": True}, format="json"
        )
        self.assertEqual(resp.status_code, 400)


# ── ratings ───────────────────────────────────────────────────────────────────


class RatingTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = _seller(username="r_seller", email="rs@test.com")
        self.buyer = _buyer(username="r_buyer", email="rb@test.com")
        self.other = _buyer(username="r_other", email="ro@test.com")
        self.project = _published_project(self.seller, "Rateable")
        from orders.models import Order

        Order.objects.create(
            buyer=self.buyer,
            project=self.project,
            seller=self.seller,
            price_at_purchase="10000.00",
            platform_fee_amount="1000.00",
            seller_earning_amount="9000.00",
            status=Order.Status.PAID,
        )
        self.url = reverse("rating_upsert", args=[self.project.slug])

    def _auth(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(user))

    def test_create_rating(self):
        self._auth(self.buyer)
        resp = self.client.post(self.url, {"score": 5, "review_text": "Great!"}, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["score"], 5)
        self.assertEqual(data["username"], "r_buyer")

    def test_non_buyer_cannot_rate(self):
        self._auth(self.other)
        resp = self.client.post(self.url, {"score": 4}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_seller_cannot_rate_own_project(self):
        self._auth(self.seller)
        resp = self.client.post(self.url, {"score": 5}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_upsert_updates_existing_rating(self):
        self._auth(self.buyer)
        self.client.post(self.url, {"score": 3}, format="json")
        resp = self.client.post(self.url, {"score": 5}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["score"], 5)
        ratings = Rating.objects.filter(project=self.project, user=self.buyer)
        self.assertEqual(ratings.count(), 1)

    def test_patch_updates_rating(self):
        self._auth(self.buyer)
        self.client.post(self.url, {"score": 2}, format="json")
        resp = self.client.patch(self.url, {"score": 4}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["score"], 4)

    def test_delete_rating(self):
        self._auth(self.buyer)
        self.client.post(self.url, {"score": 3}, format="json")
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(Rating.objects.count(), 0)

    def test_rating_cache_updates_after_create(self):
        self.assertEqual(self.project.average_rating, 0.0)
        self.assertEqual(self.project.rating_count, 0)
        self._auth(self.buyer)
        self.client.post(self.url, {"score": 4}, format="json")
        self.project.refresh_from_db()
        self.assertEqual(self.project.average_rating, 4.0)
        self.assertEqual(self.project.rating_count, 1)

    def test_rating_cache_updates_after_update(self):
        self._auth(self.buyer)
        self.client.post(self.url, {"score": 2}, format="json")
        self.client.post(self.url, {"score": 5}, format="json")
        self.project.refresh_from_db()
        self.assertEqual(self.project.average_rating, 5.0)
        self.assertEqual(self.project.rating_count, 1)

    def test_rating_cache_updates_after_delete(self):
        self._auth(self.buyer)
        self.client.post(self.url, {"score": 3}, format="json")
        self.client.delete(self.url)
        self.project.refresh_from_db()
        self.assertEqual(self.project.average_rating, 0.0)
        self.assertEqual(self.project.rating_count, 0)

    def test_score_validation_below_1(self):
        self._auth(self.buyer)
        resp = self.client.post(self.url, {"score": 0}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_score_validation_above_5(self):
        self._auth(self.buyer)
        resp = self.client.post(self.url, {"score": 6}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_score_validation_negative(self):
        self._auth(self.buyer)
        resp = self.client.post(self.url, {"score": -1}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_score_validation_decimal(self):
        self._auth(self.buyer)
        resp = self.client.post(self.url, {"score": 3.5}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_get_ratings(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_rate_unauthenticated_returns_401(self):
        resp = self.client.post(self.url, {"score": 4}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_patch_without_existing_rating_returns_404(self):
        self._auth(self.buyer)
        resp = self.client.patch(self.url, {"score": 4}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_delete_without_existing_rating_returns_404(self):
        self._auth(self.buyer)
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_multiple_buyers_can_rate(self):
        buyer2 = _buyer(username="r_buyer2", email="rb2@test.com")
        Order.objects.create(
            buyer=buyer2,
            project=self.project,
            seller=self.seller,
            price_at_purchase="10000.00",
            platform_fee_amount="1000.00",
            seller_earning_amount="9000.00",
            status=Order.Status.PAID,
        )
        self._auth(self.buyer)
        self.client.post(self.url, {"score": 5}, format="json")
        self._auth(buyer2)
        self.client.post(self.url, {"score": 3}, format="json")
        self.project.refresh_from_db()
        self.assertEqual(self.project.average_rating, 4.0)
        self.assertEqual(self.project.rating_count, 2)


# ── comments ──────────────────────────────────────────────────────────────────


class CommentTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = _seller(username="c_seller", email="cs@test.com")
        self.user = _buyer(username="c_user", email="cu@test.com")
        self.project = _published_project(self.seller, "Commentable")
        self.url = reverse("comment_list_create", args=[self.project.slug])

    def test_list_comments_public(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_create_comment_authenticated(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        resp = self.client.post(self.url, {"body": "Nice project!"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["body"], "Nice project!")
        self.assertEqual(resp.json()["username"], "c_user")

    def test_create_comment_unauthenticated_returns_401(self):
        resp = self.client.post(self.url, {"body": "Hello"}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_comment_includes_user_details(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.user))
        resp = self.client.post(self.url, {"body": "Great"}, format="json")
        data = resp.json()
        self.assertIn("username", data)
        self.assertIn("avatar_url", data)
        self.assertIn("created_at", data)


# ── Q&A ───────────────────────────────────────────────────────────────────────


class QATest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = _seller(username="qa_seller", email="qas@test.com")
        self.buyer = _buyer(username="qa_buyer", email="qab@test.com")
        self.other = _buyer(username="qa_other", email="qao@test.com")
        self.project = _published_project(self.seller, "QA Project")
        self.url = reverse("qa_list_create", args=[self.project.slug])

    def test_list_qa_public(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_ask_question_authenticated(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.buyer))
        resp = self.client.post(self.url, {"question": "Does this work on Windows?"}, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["question"], "Does this work on Windows?")
        self.assertEqual(data["author"]["username"], "qa_buyer")
        self.assertIsNone(data["answer"])

    def test_seller_can_answer_question(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.buyer))
        create_resp = self.client.post(self.url, {"question": "Support PostgreSQL?"}, format="json")
        qa_id = create_resp.json()["id"]

        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller))
        answer_url = reverse("qa_answer", args=[self.project.slug, qa_id])
        resp = self.client.post(
            answer_url, {"answer": "Yes, PostgreSQL 14+ is fully supported."}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["answer"], "Yes, PostgreSQL 14+ is fully supported.")
        self.assertIsNotNone(resp.json()["answered_at"])

    def test_non_seller_cannot_answer_question(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.buyer))
        create_resp = self.client.post(self.url, {"question": "Is Docker required?"}, format="json")
        qa_id = create_resp.json()["id"]

        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.other))
        answer_url = reverse("qa_answer", args=[self.project.slug, qa_id])
        resp = self.client.post(answer_url, {"answer": "I think so."}, format="json")
        self.assertEqual(resp.status_code, 403)


# ── category ──────────────────────────────────────────────────────────────────


class CategoryModelTest(TestCase):
    def test_category_str(self):
        cat = Category.objects.create(name="Test Category", slug="test-category")
        self.assertEqual(str(cat), "Test Category")

    def test_category_slug_auto_generated(self):
        cat = Category.objects.create(name="Test Category")
        self.assertEqual(cat.slug, "test-category")

    def test_category_unique_name(self):
        Category.objects.create(name="Unique Category")
        with self.assertRaises(IntegrityError):
            Category.objects.create(name="Unique Category")


# ── IDOR prevention regression tests ──────────────────────────────────────────


class IDORPreventionTest(TestCase):
    """Regression: User A must not be able to access/modify User B's resources."""

    def setUp(self):
        self.client = APIClient()
        self.seller_a = _onboard(User.objects.create_user(username="seller_a"))
        self.seller_a.is_seller = True
        self.seller_a.github_token_encrypted = "dummy"
        self.seller_a.save()

        self.seller_b = _onboard(User.objects.create_user(username="seller_b"))
        self.seller_b.is_seller = True
        self.seller_b.github_token_encrypted = "dummy"
        self.seller_b.save()

        self.cat = Category.objects.create(name="Tools", slug="tools")
        self.project_b = Project.objects.create(
            seller=self.seller_b,
            title="Seller B's Project",
            description="A project by B",
            price=100,
            github_repo_full_name="b/repo",
            category=self.cat,
            status=Project.Status.DRAFT,
        )

    def test_seller_a_cannot_view_seller_b_project(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller_a))
        resp = self.client.get(f"/api/listings/projects/{self.project_b.pk}/")
        self.assertEqual(resp.status_code, 404)

    def test_seller_a_cannot_edit_seller_b_project(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller_a))
        resp = self.client.patch(
            f"/api/listings/projects/{self.project_b.pk}/",
            {"title": "Hacked"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_seller_a_cannot_delete_seller_b_project(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller_a))
        resp = self.client.delete(f"/api/listings/projects/{self.project_b.pk}/")
        self.assertEqual(resp.status_code, 404)

    def test_seller_a_cannot_submit_seller_b_project(self):
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller_a))
        resp = self.client.post(
            f"/api/listings/projects/{self.project_b.pk}/submit/",
            {"accept_terms": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)


# ── Input validation regression tests ─────────────────────────────────────────


class ProjectInputValidationTest(TestCase):
    """Regression: serializer must reject invalid input with 400, not 500."""

    def setUp(self):
        self.client = APIClient()
        self.seller = _onboard(User.objects.create_user(username="validator"))
        self.seller.is_seller = True
        self.seller.github_token_encrypted = "dummy"
        self.seller.save()
        self.client.credentials(HTTP_AUTHORIZATION=_bearer(self.seller))
        self.cat = Category.objects.create(name="Tools", slug="tools")

    def test_negative_price_rejected(self):
        resp = self.client.post(
            reverse("project_list_create"),
            {
                "title": "Test",
                "description": "Test description",
                "price": "-50",
                "github_repo_full_name": "test/repo",
                "category": self.cat.pk,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_zero_price_rejected(self):
        resp = self.client.post(
            reverse("project_list_create"),
            {
                "title": "Test",
                "description": "Test description",
                "price": "0",
                "github_repo_full_name": "test/repo",
                "category": self.cat.pk,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_blank_title_rejected(self):
        resp = self.client.post(
            reverse("project_list_create"),
            {
                "title": "   ",
                "description": "Test description",
                "price": "100",
                "github_repo_full_name": "test/repo",
                "category": self.cat.pk,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_hex_color_rejected(self):
        project = Project.objects.create(
            seller=self.seller,
            title="Test",
            description="Test",
            price=100,
            github_repo_full_name="test/repo",
            status=Project.Status.DRAFT,
        )
        resp = self.client.patch(
            f"/api/listings/projects/{project.pk}/",
            {"accent_color": "not-a-color"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_oversized_tags_rejected(self):
        resp = self.client.post(
            reverse("project_list_create"),
            {
                "title": "Test",
                "description": "Test description",
                "price": "100",
                "github_repo_full_name": "test/repo",
                "category": self.cat.pk,
                "tags": [f"tag{i}" for i in range(25)],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
